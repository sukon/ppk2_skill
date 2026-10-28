# Tool Registry

This file is the authoritative index of reusable developer tools and repeatable project procedures.
Before creating a helper script or running testing, flashing, debugging, capture, conversion, or packaging workflows, search this file first and reuse or extend an existing capability when practical.

## Capability Index

| Capability | Tool / Procedure | Path / Command | Notes |
| --- | --- | --- | --- |
| PPK2 整機電流量測（電源模式） | ppk2_measure | `python tools/ppk2_measure.py --mv 3700` | 需要接上 PPK2 與 `ppk2-api` |
| PPK2 供電電壓確認 | ppk2_measure --check | `python tools/ppk2_measure.py --check` | 只讀回 VDD，不送出設定 |
| PPK2 電流時段平均（離線） | ppk2_measure --avg | `python tools/ppk2_measure.py --avg CSV --start HH:MM:SS --end HH:MM:SS` | 不連儀器、不需第三方套件 |
| PPK2 電流區段分析（離線） | ppk2_measure --bursts | `python tools/ppk2_measure.py --bursts CSV --threshold 1000 --gap 50` | 1ms 平均值上的門檻區段，非瞬時峰值 |
| 電流量測程序手冊 | PPK2 測量程序 | `ppk.md` | 接線、電壓選擇、程序、陷阱、歷史基準 |
| PPK2 工具離線測試 | unittest | `python -m unittest discover -s tests` | 不需要儀器 |

## Tools and Procedures

### PPK2 整機電流量測（電源模式）

**Purpose:** 以 Nordic PPK2 的電源模式（source meter）供電給待測裝置（DUT），量測平均／最小／最大電流，並把每 1ms 平均值存成 CSV；可一次供電、連續記錄，再用離線模式取各狀態時段的平均。

**Path / command:** `tools/ppk2_measure.py`

**Use when:**
- 要量待機／接收／發送狀態的電流
- 要比較韌體修改前後的耗電
- 要找高於門檻的電流區段（發送、喚醒、週邊動作）

**Usage:**
```bash
python tools/ppk2_measure.py --check                                   # 只讀回目前 VDD
python tools/ppk2_measure.py --mv 3700 --power-on --seconds 60         # 供電並量測 60 秒
python tools/ppk2_measure.py --mv 3700 --power-on --seconds 0 --label session > temp/ppk2_session.log
python tools/ppk2_measure.py --avg temp/ppk2/<時間>_session.csv --start 12:47:20 --end 12:48:20
python tools/ppk2_measure.py --bursts temp/ppk2/<時間>_session.csv --threshold 1000 --gap 50
```

**Inputs / arguments:**
- `--check`: 只讀回目前 VDD，不送出任何設定（唯讀路徑）
- `--mv`: 供電電壓 mV（量測時必填）
- `--mv-range MIN:MAX`: 允許的電壓範圍（預設 `3000:4200`，鋰電池範圍）；超出即中止，不會自動修正。依 DUT 文件的輸入電壓範圍決定，不要用電池種類猜；放寬前先確認文件允許
- `--seconds`: 量測秒數（預設 60；`0` = 持續到中斷為止）
- `--power-on`: 送出 DUT 供電開啟（裝置原本斷電時才需要）
- `--port`: PPK2 序列埠（預設自動尋找）
- `--csv`: 1ms 平均值輸出檔
- `--out-dir`: 輸出目錄（預設 `temp/ppk2`）
- `--label`: 記在 CSV 檔名與結果的說明
- `--avg CSV` + `--start`／`--end`（HH:MM:SS，含頭含尾）：離線計算時段平均
- `--bursts CSV` + `--threshold`（uA，預設 1000）／`--gap`（ms，預設 50）：離線找出高於門檻的區段

**Outputs:**
- 終端機：量測中每 5 秒印近況；結束時印平均（mA、uA）、最小、最大
- 持久輸出：`temp/ppk2/<時間>[_label].csv`（欄位 `time,uA`，時間 `HH:MM:SS.mmm`，每列 1ms 平均值）
- 離線模式的輸出只印到終端機，不寫檔

**Requirements:**
- 量測模式：`pip install -r requirements.txt`（ppk2-api、pyserial）；PPK2 USB 接電腦
- 離線模式（`--avg`／`--bursts`）與 `--help`：不需要任何第三方套件

**Notes / constraints:**
- 同一時間只能有一個程式連 PPK2：量測前關閉 nRF Connect Power Profiler。Power Profiler 關閉時可能沒停止串流，工具會先送停止量測並清空緩衝再讀 metadata。
- 序列埠關閉時 PPK2 會自行關閉 DUT 輸出（lg_mesh 實測），因此要多狀態量測請用 `--seconds 0` 持續記錄、再以 `--avg` 取時段平均；結束記錄時裝置會斷電。
- 設定電壓後會讀回 metadata 的 `VDD` 確認，不符即中止；`--power-on` 開電前再確認一次，讀不到 VDD 就拒絕開電。以上行為有假裝置測試涵蓋（唯讀、不符、讀不到、快樂路徑）。
- CSV 的時間戳由取樣數推算（100kHz → 每列 1ms），不受 USB 傳輸延遲影響，可用來對齊別的工具記錄。
- `--bursts` 找的是「1ms 平均值上的門檻區段」，不是瞬時峰值，也不一定是 TX；1ms 內的尖峰會被平均掉，報告值是下限。
- 量測前請確認 DUT 沒有其他供電路徑（USB、充電座），避免反灌；程序細節與已知陷阱見 `ppk.md`。
- 來源：LG-M202（`C:\Working\Lora\lg_mesh`）的 `tools/ppk2_measure.py`（commit `7abfd22`，抽出於 2026-10-28）。該 repo 保留自己的副本，未受本工作區影響。

### PPK2 測量程序（接線、電壓、狀態切換、陷阱）

**Purpose:** 把「怎麼接、量哪個狀態、怎麼切換狀態、有哪些已知陷阱」寫成可重複的程序，避免每次重新發明。

**Path / command:** `ppk.md`

**Use when:**
- 第一次用 PPK2 量某個新裝置
- 要量多種狀態但不想反覆斷電開機
- 要解讀 CSV 的數值或設計電流基準

**Usage:** 直接閱讀 `ppk.md`；第 1 節是接線與電壓安全，第 3 節是程序，第 5 節是通用陷阱，第 6 節是 LG-M202 的歷史基準（僅供對照，非通則）。

**Inputs / arguments:** 無（文件）。

**Outputs:** 無。

**Requirements:** 無。

**Notes / constraints:**
- 第 6 節的數值是 LG-M202 於 2026-10-07 的實測值，換韌體或硬體版本就可能不同，不可當通則引用。
- 本工作區建立時 PPK2 未接在電腦上，硬體行為沿用 lg_mesh 的實測紀錄，未在本工作區重新驗證。

### PPK2 工具離線測試

**Purpose:** 驗證 CSV 解析、時段查詢、區段分析與 CLI 行為，不需要接儀器。

**Path / command:** `python -m unittest discover -s tests`

**Use when:**
- 修改 `tools/ppk2_measure.py` 之後
- 想知道分析模式是否還正常（儀器不在手邊也能跑）

**Usage:**
```bash
python -m unittest discover -s tests
```

**Inputs / arguments:** 無。

**Outputs:** 測試結果（stdout）。

**Requirements:** Python 3；不需要第三方套件、不需要硬體。

**Notes / constraints:** 測試涵蓋壞行、跨午夜、時段邊界、區段合併與中斷收尾；硬體路徑以假物件取代。
