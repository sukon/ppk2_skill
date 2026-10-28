#!/usr/bin/env python3
"""以 Nordic PPK2（source meter／電源模式）量測待測裝置（DUT）的電流。

PPK2 以指定電壓供電給 DUT（模擬電池），量測平均／最小／最大電流，
並把每 1ms 平均值寫成 CSV，之後可用離線模式取時段平均或找高於門檻的電流區段。

三種模式：
  1. 量測（需要 PPK2 接在電腦上）：
       python tools/ppk2_measure.py --check
       python tools/ppk2_measure.py --mv 3700 --power-on --seconds 60
  2. 離線取時段平均（不需要儀器、不需要第三方套件）：
       python tools/ppk2_measure.py --avg CSV --start HH:MM:SS --end HH:MM:SS
  3. 離線找電流區段（不需要儀器、不需要第三方套件）：
       python tools/ppk2_measure.py --bursts CSV --threshold 1000 --gap 50

安全：--mv 超出允許範圍會直接中止，不會自動修正也不會夾擠。
      預設範圍 3000–4200mV 是鋰電池範圍，不代表所有 DUT 都安全；
      請先確認 DUT 可承受的電壓，必要時用 --mv-range 明確放寬。
量測：同一時間只能有一個程式連 PPK2（量測前請關閉 nRF Connect Power Profiler）。
"""
import argparse
import contextlib
import sys
import time
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = WORKSPACE / 'temp' / 'ppk2'
MV_MIN = 3000          # 預設範圍為鋰電池 3.0–4.2V；用 --mv-range 明確放寬
MV_MAX = 4200
RATE = 100000          # PPK2 取樣率 100kHz
BIN = 100              # 每 100 筆（1ms）取一次平均
DAY = 86400.0


# ---------------------------------------------------------------- 離線分析（純函式，不需硬體）

def parse_ts(text):
    """'HH:MM:SS.mmm' 或 'HH:MM:SS' → 當日秒數（float）；格式錯誤回 None。"""
    text = (text or '').strip()
    if not text:
        return None
    parts = text.split(':')
    if len(parts) not in (2, 3):
        return None
    try:
        h = int(parts[0])
        m = int(parts[1])
        s = float(parts[2]) if len(parts) == 3 else 0.0
    except ValueError:
        return None
    if not (0 <= h < 24 and 0 <= m < 60 and 0.0 <= s < 60.0):
        return None
    return h * 3600 + m * 60 + s


def fmt_clock(seconds):
    """相對秒 → 'HH:MM:SS.mmm'（跨日自動進位）。"""
    try:
        total_ms = int(round((seconds % DAY) * 1000))
        h, rem = divmod(total_ms, 3600000)
        m, rem = divmod(rem, 60000)
        s, ms = divmod(rem, 1000)
    except (OverflowError, ValueError):
        return '??:??:??.???'
    return f'{h:02d}:{m:02d}:{s:02d}.{ms:03d}'


class Session:
    """一次量測的 1ms 平均值資料。"""

    def __init__(self, rows, skipped, base_clock):
        self.rows = rows          # [(相對秒, uA)]
        self.skipped = skipped    # 略過的壞行數
        self.base_clock = base_clock  # 第一筆的當日秒數（時段查詢用）


def load_session(path):
    """讀 1ms 平均 CSV → Session；略過標題與壞行。"""
    rows = []
    skipped = 0
    day = 0.0
    prev_clock = None
    base_clock = None
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            for lineno, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                if lineno == 0 and line.lower().startswith('time'):
                    continue
                t_text, _, v_text = line.partition(',')
                clock = parse_ts(t_text)
                try:
                    value = float(v_text)
                except ValueError:
                    skipped += 1
                    continue
                if clock is None:
                    skipped += 1
                    continue
                if prev_clock is not None and clock < prev_clock:
                    if prev_clock - clock > 3600:      # 跨午夜
                        day += DAY
                    else:                              # 同一秒內順序顛倒：丟棄
                        skipped += 1
                        continue
                prev_clock = clock
                if base_clock is None:
                    base_clock = clock
                rows.append((round(day + clock - base_clock, 6), value))
    except OSError as exc:
        raise SystemExit(f'無法讀取 CSV：{path}（{exc}）') from exc
    if base_clock is None:
        base_clock = 0.0
    return Session(rows, skipped, base_clock)


def window_bounds(session, start=None, end=None):
    """把 HH:MM:SS 的時段換算成相對秒；只給一端時另一端不設限。"""
    s = parse_ts(start) if start else None
    e = parse_ts(end) if end else None
    if s is None and e is None:
        return None, None
    base = session.base_clock

    def to_rel(clock):
        return (clock - base) % DAY

    s_rel = to_rel(s) if s is not None else None
    e_rel = to_rel(e) if e is not None else None
    if s_rel is not None and e_rel is not None and e_rel < s_rel:
        e_rel += DAY                      # 跨午夜的時段
    return s_rel, e_rel


def summarize(session, start=None, end=None):
    """取指定時段（HH:MM:SS，含頭含尾）的平均／最小／最大；回傳 dict。"""
    s_rel, e_rel = window_bounds(session, start, end)
    values = []
    for rel, value in session.rows:
        if s_rel is not None and rel < s_rel:
            continue
        if e_rel is not None and rel > e_rel:
            continue
        values.append(value)
    if not values:
        return None
    return {
        'avg': sum(values) / len(values),
        'min': min(values),
        'max': max(values),
        'count': len(values),
        'seconds': len(values) / 1000.0,
    }


def find_bursts(session, threshold_ua, gap_ms=50.0):
    """在 1ms 平均值上找高於門檻的區段；間隔小於 gap_ms 的區段合併成一個。

    回傳 [{'start': 相對秒, 'end': 相對秒, 'duration_ms':, 'samples':, 'peak_ua':, 'mean_ua':}]
    mean_ua 是整個區段（含低於門檻的間隔）所有取樣的平均。
    這是「1ms 平均值上的門檻區段」，不是瞬時峰值，也不一定是 TX。
    """
    gap_s = gap_ms / 1000.0
    runs = []
    current = None
    for rel, value in session.rows:
        if value >= threshold_ua:
            if current is None:
                current = {'start': rel, 'end': rel, 'peak_ua': value}
            else:
                current['end'] = rel
                current['peak_ua'] = max(current['peak_ua'], value)
        else:
            if current is not None:
                runs.append(current)
                current = None
    if current is not None:
        runs.append(current)

    merged = []
    for run in runs:
        if merged and (run['start'] - merged[-1]['end']) <= gap_s:
            prev = merged[-1]
            prev['end'] = run['end']
            prev['peak_ua'] = max(prev['peak_ua'], run['peak_ua'])
        else:
            merged.append(dict(run))
    for run in merged:
        run['duration_ms'] = round((run['end'] - run['start']) * 1000.0 + 1.0, 3)
        span = [v for rel, v in session.rows if run['start'] <= rel <= run['end']]
        run['samples'] = len(span)
        run['mean_ua'] = sum(span) / len(span) if span else 0.0
    return merged


def print_summary(result, label_start, label_end, skipped=0):
    avg = result['avg']
    print(f'{label_start}–{label_end}：平均 {avg / 1000:.3f}mA（{avg:.1f}uA），'
          f'1ms 最小 {result["min"]:.1f}uA，最大 {result["max"] / 1000:.2f}mA，'
          f'{result["seconds"]:.1f} 秒')
    if skipped:
        print(f'（略過 {skipped} 筆格式錯誤的資料列）')


# ---------------------------------------------------------------- 硬體（量測模式）

def ppk2_module():
    """延後載入 ppk2-api，讓離線分析模式不需要第三方套件。"""
    try:
        from ppk2_api import ppk2_api
    except ImportError as exc:
        raise SystemExit('找不到 ppk2-api，請先安裝：pip install -r requirements.txt') from exc
    return ppk2_api


def find_port():
    """自動尋找 PPK2 序列埠。"""
    ports = ppk2_module().PPK2_MP.list_devices()
    if not ports:
        sys.exit('找不到 PPK2（確認 USB 已接、驅動正常）')
    p = ports[0]
    return p[0] if isinstance(p, (tuple, list)) else p


def read_metadata(ppk):
    """停止串流、清空殘留取樣後讀 metadata 文字；讀不到回 ''。

    Power Profiler 關閉時可能沒停止量測，序列埠裡留著二進位取樣，
    函式庫的 _read_metadata 會解碼失敗，所以自己讀到 END 為止。
    """
    ppk2 = ppk2_module()
    ppk._write_serial((ppk2.PPK2_Command.AVERAGE_STOP, ))     # 只停量測，不影響供電
    time.sleep(0.3)
    ppk.ser.reset_input_buffer()
    ppk._write_serial((ppk2.PPK2_Command.GET_META_DATA, ))
    raw = b''
    end = time.monotonic() + 2
    while time.monotonic() < end and b'END' not in raw:
        raw += ppk.ser.read(ppk.ser.in_waiting or 1)
    if b'END' not in raw:
        return ''
    return raw.decode('utf-8', errors='replace')


def read_vdd(ppk):
    """讀 PPK2 metadata 的 VDD（mV）；讀不到回 None。"""
    for row in read_metadata(ppk).splitlines():
        k, _, v = row.partition(': ')
        if k.strip() == 'VDD':
            try:
                return int(float(v))
            except ValueError:
                return None
    return None


def capture(ppk, csv_path, seconds, label=''):
    """量測並把每 1ms 平均值寫成 CSV；回傳統計 dict。

    ppk 只要求有 start_measuring/get_data/get_samples/stop_measuring，
    因此可以在沒有硬體的情況下用假物件測試。
    """
    total = 0.0
    count = 0
    lo = float('inf')
    hi = float('-inf')
    pend = []
    recent = []
    rows = 0
    w0 = time.time()                    # 時間戳以取樣數推算（100kHz），不受 USB 傳輸延遲影響
    try:
        with open(csv_path, 'w', encoding='utf-8', newline='') as f:
            f.write('time,uA\n')
            ppk.start_measuring()
            t0 = time.monotonic()
            print(f'{time.strftime("%H:%M:%S")} 量測 '
                  f'{f"{seconds:g} 秒" if seconds else "到中斷為止"}，記錄到 {csv_path}', flush=True)
            try:
                while not seconds or time.monotonic() - t0 < seconds:
                    buf = ppk.get_data()
                    if buf == b'':
                        time.sleep(0.01)
                        continue
                    samples, _ = ppk.get_samples(buf)
                    for s in samples:
                        total += s
                        count += 1
                        lo = min(lo, s)
                        hi = max(hi, s)
                        pend.append(s)
                        if len(pend) == BIN:
                            v = sum(pend) / BIN
                            pend = []
                            t = w0 + rows / 1000
                            f.write(f'{time.strftime("%H:%M:%S", time.localtime(t))}.'
                                    f'{int(t * 1000) % 1000:03d},{v:.2f}\n')
                            rows += 1
                            recent.append(v)
                            if len(recent) >= 5000:     # 每 5 秒顯示一次近況
                                print(f'{time.strftime("%H:%M:%S")} 近 5 秒平均 '
                                      f'{sum(recent) / len(recent):.1f}uA', flush=True)
                                recent = []
                                f.flush()
            except KeyboardInterrupt:
                print(f'\n{time.strftime("%H:%M:%S")} 收到中斷，保留已記錄的資料', flush=True)
            finally:
                f.flush()
                ppk.stop_measuring()
    except OSError as exc:
        raise SystemExit(f'無法寫入 CSV：{csv_path}（{exc}）') from exc

    if count == 0:
        sys.exit('沒有收到任何取樣')
    return {
        'avg': total / count,
        'min': lo,
        'max': hi,
        'count': count,
        'seconds': count / RATE,
        'rows': rows,
    }


def run_measurement(args):
    """量測模式：接上 PPK2、設電壓、供電、量測。"""
    if args.mv is None and not args.check:
        sys.exit('需要 --mv（或用 --check 只讀回電壓）')
    lo, hi = args.mv_range
    if args.mv is not None and not lo <= args.mv <= hi:
        sys.exit(f'電壓 {args.mv}mV 超出允許範圍 {lo}–{hi}mV，中止（不會自動修正）')

    port = args.port or find_port()
    ppk2 = ppk2_module()
    ppk = ppk2.PPK2_MP(port, buffer_max_size_seconds=5, buffer_chunk_seconds=0.1,
                      timeout=1, write_timeout=1)
    md = read_metadata(ppk)
    if not md or not ppk._parse_metadata(md):
        sys.exit('讀不到 PPK2 metadata（校正值），中止')
    before = read_vdd(ppk)
    print(f'{time.strftime("%H:%M:%S")} PPK2 目前 VDD：'
          f'{before if before is not None else "讀不到"} mV', flush=True)
    if args.check:
        return

    ppk.use_source_meter()              # 先切電源模式再設電壓（再送一次模式不影響已開啟的輸出）
    ppk.set_source_voltage(args.mv)
    time.sleep(0.2)
    after = read_vdd(ppk)
    if after is None:
        if args.power_on:
            sys.exit('讀不到設定後的 VDD，不開啟 DUT 供電')
        print('警告：讀不到設定後的 VDD（metadata 沒有 VDD 欄位），無法確認電壓', flush=True)
    elif after != args.mv:
        sys.exit(f'設定後 VDD 讀回 {after}mV，與指定的 {args.mv}mV 不符，中止')
    else:
        print(f'{time.strftime("%H:%M:%S")} 已確認 VDD = {after}mV', flush=True)

    if args.power_on:
        if read_vdd(ppk) != args.mv:     # 開電前最後確認
            sys.exit('開電前 VDD 確認失敗，不開啟 DUT 供電')
        ppk.toggle_DUT_power('ON')
        print(f'{time.strftime("%H:%M:%S")} 已開啟 DUT 供電 {args.mv}mV', flush=True)
    print(f'{time.strftime("%H:%M:%S")} PPK2 {port}，電源模式 {args.mv}mV', flush=True)

    if args.csv:
        csv_path = Path(args.csv)
    else:
        out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_OUT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        tag = f'_{args.label}' if args.label else ''
        csv_path = out_dir / (time.strftime('%Y%m%d-%H%M%S') + tag + '.csv')

    result = capture(ppk, csv_path, args.seconds, args.label)
    avg = result['avg']
    print(f'{time.strftime("%H:%M:%S")} 平均 {avg / 1000:.3f}mA（{avg:.1f}uA），'
          f'最小 {result["min"]:.1f}uA，最大 {result["max"] / 1000:.2f}mA，'
          f'{result["seconds"]:.1f} 秒' + (f'，{args.label}' if args.label else ''))
    print(f'1ms 平均值：{csv_path}')


# ---------------------------------------------------------------- 命令列

def parse_mv_range(text):
    try:
        lo_text, _, hi_text = text.partition(':')
        lo, hi = int(lo_text), int(hi_text)
    except ValueError:
        raise argparse.ArgumentTypeError(f'格式錯誤：{text}（要像 3000:4200）') from None
    if not 0 < lo < hi:
        raise argparse.ArgumentTypeError(f'範圍不合法：{text}')
    return (lo, hi)


def build_parser():
    ap = argparse.ArgumentParser(
        description='PPK2 電源模式電流量測（量測 / 離線時段平均 / 離線電流區段）')
    ap.add_argument('--port', help='PPK2 序列埠（預設自動尋找）')
    ap.add_argument('--check', action='store_true', help='只讀回目前 VDD，不送出任何設定')
    ap.add_argument('--mv', type=int, help='供電電壓 mV（量測時必填）')
    ap.add_argument('--mv-range', type=parse_mv_range, default=(MV_MIN, MV_MAX),
                    help=f'允許的供電電壓範圍 MIN:MAX（預設 {MV_MIN}:{MV_MAX}，'
                         f'超出即中止；放寬前請確認 DUT 可承受的電壓）')
    ap.add_argument('--seconds', type=float, default=60,
                    help='量測秒數（預設 60；0 = 持續到中斷為止）')
    ap.add_argument('--power-on', action='store_true', help='送出 DUT 供電開啟（裝置原本斷電時才需要）')
    ap.add_argument('--csv', help='1ms 平均值輸出檔（預設 <workspace>/temp/ppk2/<時間>.csv）')
    ap.add_argument('--out-dir', help='輸出目錄（預設 <workspace>/temp/ppk2）')
    ap.add_argument('--label', default='', help='記在 CSV 檔名與結果的說明')
    ap.add_argument('--avg', metavar='CSV', help='不連 PPK2，計算 CSV 中 --start／--end 時段的平均')
    ap.add_argument('--start', help='--avg 起始時間 HH:MM:SS')
    ap.add_argument('--end', help='--avg 結束時間 HH:MM:SS')
    ap.add_argument('--bursts', metavar='CSV',
                    help='不連 PPK2，找出 CSV 中高於 --threshold 的 1ms 平均值區段')
    ap.add_argument('--threshold', type=float, default=1000.0,
                    help='--bursts 的門檻 uA（預設 1000）')
    ap.add_argument('--gap', type=float, default=50.0,
                    help='--bursts 合併區段的間隔 ms（預設 50）')
    return ap


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is not None:
            with contextlib.suppress(AttributeError, ValueError):
                reconfigure(encoding='utf-8')
    args = build_parser().parse_args(argv)

    if args.avg and args.bursts:
        sys.exit('--avg 與 --bursts 只能擇一')
    if args.avg:
        session = load_session(args.avg)
        if not session.rows:
            sys.exit('CSV 沒有可用的資料')
        result = summarize(session, args.start, args.end)
        if result is None:
            sys.exit('時段內沒有資料')
        print_summary(result, args.start or '開頭', args.end or '結尾', session.skipped)
        return
    if args.bursts:
        session = load_session(args.bursts)
        if not session.rows:
            sys.exit('CSV 沒有可用的資料')
        bursts = find_bursts(session, args.threshold, args.gap)
        print(f'門檻 {args.threshold:g}uA、合併間隔 {args.gap:g}ms，'
              f'共 {len(bursts)} 個區段（1ms 平均值）')
        for i, b in enumerate(bursts, 1):
            clock = fmt_clock(session.base_clock + b['start'])
            print(f'#{i}  {clock}  持續 {b["duration_ms"]:.0f}ms  '
                  f'1ms 平均峰值 {b["peak_ua"] / 1000:.2f}mA（{b["peak_ua"]:.0f}uA）  '
                  f'區間平均 {b["mean_ua"] / 1000:.2f}mA')
        print('提醒：以上是 1ms 平均值上的門檻區段，不是瞬時峰值，也不一定是 TX。')
        if session.skipped:
            print(f'（略過 {session.skipped} 筆格式錯誤的資料列）')
        return
    run_measurement(args)


if __name__ == '__main__':
    main()
