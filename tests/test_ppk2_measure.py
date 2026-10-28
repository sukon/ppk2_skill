#!/usr/bin/env python3
"""ppk2_measure.py 的離線測試（不需要 PPK2 接在電腦上）。

執行：python -m unittest discover -s tests
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))

import ppk2_measure as ppk  # type: ignore[import-not-found]  # noqa: E402


def write_csv(rows, header: str | None = 'time,uA'):
    """rows: [(timestamp_text, value)] → 暫存 CSV 路徑。"""
    path = tempfile.mkstemp(suffix='.csv')[1]
    with open(path, 'w', encoding='utf-8', newline='') as f:
        if header is not None:
            f.write(header + '\n')
        for t, v in rows:
            f.write(f'{t},{v}\n')
    return path


def ms(i):
    """i 毫秒後的時間戳（測試資料都在 12:00:00.000 之後 1 秒內）。"""
    return f'12:00:00.{i:03d}'


class FakePPK:
    """假的 PPK2：符合 capture() 用到的介面（start/get_data/get_samples/stop）。"""

    def __init__(self, samples, interrupt_after=None, empty_after=None):
        self.samples = list(samples)
        self.interrupt_after = interrupt_after
        self.empty_after = empty_after
        self.calls = 0
        self.started = False
        self.stopped = False

    def start_measuring(self):
        self.started = True

    def stop_measuring(self):
        self.stopped = True

    def get_data(self):
        self.calls += 1
        if self.interrupt_after is not None and self.calls > self.interrupt_after:
            raise KeyboardInterrupt
        if self.empty_after is not None and self.calls > self.empty_after:
            return b''
        return b'\x01\x02'

    def get_samples(self, buf):
        return (self.samples, None)


class ParseTsTest(unittest.TestCase):
    def test_valid(self):
        self.assertAlmostEqual(ppk.parse_ts('12:47:20'), 12 * 3600 + 47 * 60 + 20)
        self.assertAlmostEqual(ppk.parse_ts('12:47:20.125'), 12 * 3600 + 47 * 60 + 20.125)
        self.assertAlmostEqual(ppk.parse_ts('00:00:00'), 0.0)

    def test_invalid(self):
        for bad in ('', 'abc', '25:00:00', '12:60:00', '12:47:99', '12', '12:47:xx'):
            self.assertIsNone(ppk.parse_ts(bad), bad)


class LoadSessionTest(unittest.TestCase):
    def test_empty_file(self):
        path = write_csv([], header=None)
        session = ppk.load_session(path)
        self.assertEqual(session.rows, [])
        self.assertEqual(session.skipped, 0)

    def test_header_skipped(self):
        path = write_csv([('12:00:00.000', 100.0)])
        session = ppk.load_session(path)
        self.assertEqual(len(session.rows), 1)
        self.assertAlmostEqual(session.rows[0][0], 0.0)

    def test_malformed_rows_skipped(self):
        path = write_csv([('12:00:00.000', 100.0), ('oops', 1.0), ('12:00:01.000', 'x'),
                          ('12:00:02.000', 200.0)])
        session = ppk.load_session(path)
        self.assertEqual(len(session.rows), 2)
        self.assertEqual(session.skipped, 2)

    def test_relative_time_and_irregular_timestamps(self):
        path = write_csv([('12:00:00.500', 1.0), ('12:00:01.500', 2.0), ('12:00:03.500', 3.0)])
        session = ppk.load_session(path)
        self.assertAlmostEqual(session.rows[0][0], 0.0)
        self.assertAlmostEqual(session.rows[1][0], 1.0)
        self.assertAlmostEqual(session.rows[2][0], 3.0)

    def test_midnight_rollover(self):
        path = write_csv([('23:59:59.500', 1.0), ('00:00:00.500', 2.0), ('00:00:01.500', 3.0)])
        session = ppk.load_session(path)
        self.assertAlmostEqual(session.rows[0][0], 0.0)
        self.assertAlmostEqual(session.rows[1][0], 1.0)
        self.assertAlmostEqual(session.rows[2][0], 2.0)

    def test_out_of_order_row_dropped(self):
        path = write_csv([('12:00:02.000', 1.0), ('12:00:01.000', 2.0), ('12:00:03.000', 3.0)])
        session = ppk.load_session(path)
        self.assertEqual(len(session.rows), 2)
        self.assertEqual(session.skipped, 1)


class SummarizeTest(unittest.TestCase):
    def setUp(self):
        self.path = write_csv([
            ('12:00:00.000', 100.0),
            ('12:00:01.000', 200.0),
            ('12:00:02.000', 300.0),
            ('12:00:03.000', 400.0),
        ])
        self.session = ppk.load_session(self.path)

    def test_full_range(self):
        result = ppk.summarize(self.session)
        self.assertAlmostEqual(result['avg'], 250.0)
        self.assertAlmostEqual(result['min'], 100.0)
        self.assertAlmostEqual(result['max'], 400.0)
        self.assertEqual(result['count'], 4)
        self.assertAlmostEqual(result['seconds'], 0.004)

    def test_window_inclusive(self):
        result = ppk.summarize(self.session, '12:00:01', '12:00:02')
        self.assertEqual(result['count'], 2)
        self.assertAlmostEqual(result['avg'], 250.0)

    def test_window_start_only(self):
        result = ppk.summarize(self.session, '12:00:02')
        self.assertEqual(result['count'], 2)
        self.assertAlmostEqual(result['avg'], 350.0)

    def test_window_end_only(self):
        result = ppk.summarize(self.session, None, '12:00:01')
        self.assertEqual(result['count'], 2)

    def test_empty_window(self):
        self.assertIsNone(ppk.summarize(self.session, '13:00:00', '13:00:10'))

    def test_window_before_session_start(self):
        self.assertIsNone(ppk.summarize(self.session, '10:00:00', '10:00:10'))


class BurstTest(unittest.TestCase):
    def setUp(self):
        rows = []
        for i in range(20):
            rows.append((ms(i), 100.0))
        for i in range(20, 25):
            rows.append((ms(i), 5000.0))     # 5 個取樣高於門檻
        for i in range(25, 28):
            rows.append((ms(i), 100.0))      # 低於門檻 3ms
        for i in range(28, 31):
            rows.append((ms(i), 8000.0))
        for i in range(31, 40):
            rows.append((ms(i), 100.0))
        self.session = ppk.load_session(write_csv(rows))

    def test_bursts_merged_within_gap(self):
        # 兩個區段相隔 4ms（最後一個高於門檻的取樣 → 下一個高於門檻的取樣）
        bursts = ppk.find_bursts(self.session, 1000.0, gap_ms=50.0)
        self.assertEqual(len(bursts), 1)
        self.assertAlmostEqual(bursts[0]['peak_ua'], 8000.0)
        self.assertAlmostEqual(bursts[0]['duration_ms'], 11.0)   # 20→30 共 11 個取樣
        self.assertEqual(bursts[0]['samples'], 11)
        # 區間平均含中間低於門檻的取樣
        self.assertAlmostEqual(bursts[0]['mean_ua'], (5000 * 5 + 100 * 3 + 8000 * 3) / 11)

    def test_bursts_split_with_small_gap(self):
        bursts = ppk.find_bursts(self.session, 1000.0, gap_ms=1.0)
        self.assertEqual(len(bursts), 2)
        self.assertAlmostEqual(bursts[0]['peak_ua'], 5000.0)
        self.assertAlmostEqual(bursts[0]['duration_ms'], 5.0)   # 5 個 1ms 取樣
        self.assertAlmostEqual(bursts[1]['peak_ua'], 8000.0)
        self.assertAlmostEqual(bursts[1]['duration_ms'], 3.0)

    def test_threshold_filters_noise(self):
        bursts = ppk.find_bursts(self.session, 9000.0, gap_ms=50.0)
        self.assertEqual(len(bursts), 0)

    def test_clock_formatting(self):
        self.assertEqual(ppk.fmt_clock(12 * 3600 + 47 * 60 + 20.125), '12:47:20.125')
        self.assertEqual(ppk.fmt_clock(ppk.DAY + 5.5), '00:00:05.500')


class CaptureTest(unittest.TestCase):
    def test_capture_writes_csv(self):
        fd, path = tempfile.mkstemp(suffix='.csv')
        os.close(fd)
        fake = FakePPK([1000] * 250)
        stats = ppk.capture(fake, path, seconds=0.05)
        self.assertTrue(fake.started)
        self.assertTrue(fake.stopped)
        self.assertGreater(stats['count'], 0)
        self.assertGreater(stats['rows'], 0)
        text = Path(path).read_text(encoding='utf-8')
        self.assertTrue(text.startswith('time,uA\n'))
        self.assertEqual(len(text.strip().splitlines()), stats['rows'] + 1)

    def test_capture_interrupted_keeps_partial_data(self):
        fd, path = tempfile.mkstemp(suffix='.csv')
        os.close(fd)
        fake = FakePPK([500] * 200, interrupt_after=2)
        stats = ppk.capture(fake, path, seconds=0)
        self.assertTrue(fake.stopped)
        self.assertGreater(stats['count'], 0)
        text = Path(path).read_text(encoding='utf-8')   # 檔案已關閉，可讀
        self.assertTrue(text.startswith('time,uA\n'))


class FakeCommand:
    """假的 PPK2_Command 列舉。"""
    AVERAGE_STOP = 'AVERAGE_STOP'
    GET_META_DATA = 'GET_META_DATA'


class FakeSerial:
    def __init__(self, device):
        self.device = device
        self.pending_metadata = False
        self.in_waiting = 0

    def reset_input_buffer(self):
        pass

    def read(self, size):
        if not self.pending_metadata:
            return b''
        self.pending_metadata = False
        vdd = self.device.reported_vdd
        if vdd is None:
            return b'CAL: 1\nEND\n'
        return f'CAL: 1\nVDD: {vdd}\nEND\n'.encode()


class FakeDevice:
    """假的 PPK2：記錄所有送出的命令，回報可設定的 VDD。

    class 屬性由測試設定：
      vdd: 回報的 VDD（None = metadata 沒有 VDD 欄位）
      honor_set_voltage: True = set_source_voltage 後 VDD 跟著變；False = 維持原值（模擬讀回不符）
    """
    vdd: int | None = 3700
    honor_set_voltage = True

    def __init__(self):
        self.log = []
        self.reported_vdd = type(self).vdd
        self.calls = 0
        self.ser = FakeSerial(self)

    def _write_serial(self, cmd):
        self.log.append(('write_serial', cmd))
        if FakeCommand.GET_META_DATA in cmd:
            self.ser.pending_metadata = True

    def _parse_metadata(self, md):
        return {'parsed': True}

    def use_source_meter(self):
        self.log.append(('use_source_meter',))

    def set_source_voltage(self, mv):
        self.log.append(('set_source_voltage', mv))
        if type(self).honor_set_voltage and type(self).vdd is not None:
            self.reported_vdd = mv

    def toggle_DUT_power(self, state):
        self.log.append(('toggle_DUT_power', state))

    def start_measuring(self):
        self.log.append(('start_measuring',))

    def stop_measuring(self):
        self.log.append(('stop_measuring',))

    def get_data(self):
        # 限制迭代次數，避免測試中無限產生取樣
        self.calls += 1
        if self.calls > 50:
            raise KeyboardInterrupt
        return b'\x01'

    def get_samples(self, buf):
        return ([100] * 200, None)


class FakePPK2MP(FakeDevice):
    """假的 PPK2_MP：就是一台假裝置，並記住最近一台供測試檢查。"""
    last: FakeDevice | None = None

    def __init__(self, port, **kwargs):
        super().__init__()
        self.port = port
        FakePPK2MP.last = self


class FakeModule:
    """假的 ppk2_api 模組。"""
    PPK2_Command = FakeCommand
    PPK2_MP = FakePPK2MP


class CommandLineTest(unittest.TestCase):
    @staticmethod
    def run_tool(*args):
        """執行工具；Windows 主控台預設編碼是 cp950，所以明確用 UTF-8 解讀。"""
        return subprocess.run(
            [sys.executable, str(TOOLS / 'ppk2_measure.py'), *args],
            capture_output=True, text=True, encoding='utf-8', errors='replace')

    def test_help_without_hardware_dependency(self):
        code = (f'import sys; sys.path.insert(0, {str(TOOLS)!r}); import ppk2_measure; '
                "print('ppk2_api' in sys.modules)")
        out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                             encoding='utf-8', errors='replace')
        self.assertEqual(out.stdout.strip(), 'False', out.stderr)

    def test_help_runs(self):
        out = self.run_tool('--help')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn('--bursts', out.stdout)

    def test_avg_mode_offline(self):
        path = write_csv([('12:00:00.000', 1000.0), ('12:00:01.000', 2000.0)])
        out = self.run_tool('--avg', path)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn('平均', out.stdout)

    def test_bursts_mode_offline(self):
        path = write_csv([('12:00:00.000', 100.0), ('12:00:00.001', 5000.0)])
        out = self.run_tool('--bursts', path, '--threshold', '1000')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn('1ms 平均峰值', out.stdout)

    def test_voltage_out_of_range_aborts(self):
        out = self.run_tool('--mv', '5000')
        self.assertNotEqual(out.returncode, 0)
        self.assertIn('超出允許範圍', out.stderr + out.stdout)

    def test_mv_range_parser(self):
        parser = ppk.build_parser()
        args = parser.parse_args(['--mv-range', '1500:3000'])
        self.assertEqual(args.mv_range, (1500, 3000))

    def test_check_does_not_require_mv(self):
        # --check 只需讀回 VDD，不該被「需要 --mv」擋下；用假的 find_port 避免真的碰硬體
        def fake_find_port():
            raise SystemExit('NO_DEVICE')

        original = ppk.find_port
        ppk.find_port = fake_find_port
        try:
            with self.assertRaises(SystemExit) as ctx:
                ppk.main(['--check'])
            self.assertIn('NO_DEVICE', str(ctx.exception))
        finally:
            ppk.find_port = original


class HardwarePathTest(unittest.TestCase):
    """用假裝置驗證硬體路徑：--check 不送設定、VDD 不符或讀不到時不開電。"""

    def setUp(self):
        FakeDevice.vdd = 3700
        FakeDevice.honor_set_voltage = True
        FakePPK2MP.last = None
        patcher = mock.patch.object(ppk, 'ppk2_module', lambda: FakeModule)
        patcher.start()
        self.addCleanup(patcher.stop)
        sleep_patcher = mock.patch('time.sleep')
        sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

    @staticmethod
    def actions(device):
        return [entry[0] for entry in device.log]

    def test_check_is_read_only(self):
        ppk.main(['--check', '--port', 'COM_TEST'])
        device = FakePPK2MP.last
        assert device is not None
        self.assertIsNotNone(device)
        for forbidden in ('use_source_meter', 'set_source_voltage', 'toggle_DUT_power',
                          'start_measuring'):
            self.assertNotIn(forbidden, self.actions(device))

    def test_mismatched_vdd_aborts_before_power_on(self):
        FakeDevice.vdd = 3600                 # 儀器回報的 VDD 與指定值不符
        FakeDevice.honor_set_voltage = False  # set_source_voltage 不會讓 VDD 跟著變
        fd, csv_path = tempfile.mkstemp(suffix='.csv')
        os.close(fd)
        with self.assertRaises(SystemExit) as ctx:
            ppk.main(['--mv', '3700', '--power-on', '--seconds', '0.05',
                      '--csv', csv_path, '--port', 'COM_TEST'])
        self.assertIn('不符', str(ctx.exception))
        device = FakePPK2MP.last
        assert device is not None
        self.assertNotIn('toggle_DUT_power', self.actions(device))

    def test_unreadable_vdd_refuses_power_on(self):
        FakeDevice.vdd = None                 # metadata 沒有 VDD 欄位
        fd, csv_path = tempfile.mkstemp(suffix='.csv')
        os.close(fd)
        with self.assertRaises(SystemExit) as ctx:
            ppk.main(['--mv', '3700', '--power-on', '--seconds', '0.05',
                      '--csv', csv_path, '--port', 'COM_TEST'])
        self.assertIn('VDD', str(ctx.exception))
        device = FakePPK2MP.last
        assert device is not None
        self.assertNotIn('toggle_DUT_power', self.actions(device))

    def test_matching_vdd_powers_on(self):
        fd, csv_path = tempfile.mkstemp(suffix='.csv')
        os.close(fd)
        with mock.patch.object(ppk.time, 'sleep'):
            ppk.main(['--mv', '3700', '--power-on', '--seconds', '0.05',
                      '--csv', csv_path, '--port', 'COM_TEST'])
        device = FakePPK2MP.last
        assert device is not None
        self.assertIn(('toggle_DUT_power', 'ON'), device.log)
        self.assertIn(('set_source_voltage', 3700), device.log)


if __name__ == '__main__':
    unittest.main()
