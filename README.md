# ppk_use — PPK2 電流量測工作區

用 Nordic PPK2（電源模式）量測嵌入式裝置的電流：待機、接收、發送峰值，以及一次供電量多種狀態。

| 檔案 | 內容 |
| --- | --- |
| `ppk.md` | **量測手冊**：接線、電壓安全、量測程序、資料判讀、已知陷阱、LG-M202 歷史基準 |
| `TOOLS.md` | 工具索引（命令、參數、輸出位置、限制） |
| `tools/ppk2_measure.py` | 量測與離線分析工具 |
| `tests/test_ppk2_measure.py` | 離線測試（不需要儀器） |
| `skill/ppk2-current-measurement/SKILL.md` | Agent skill（Claude Code 與 pi 共用） |
| `install.ps1` | 安裝 skill 到 `~/.agents/skills` 並建立 Claude Code 的 junction |
| `requirements.txt` | 相依套件（只有量測模式需要） |
| `temp/ppk2/` | 量測 CSV 輸出（gitignored） |

## 安裝 Agent skill（Claude Code + pi）

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1          # 安裝
powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall   # 移除
```

安裝後：

- **pi**：讀 `~/.agents/skills/ppk2-current-measurement/`（真檔）
- **Claude Code**：`~/.claude/skills/ppk2-current-measurement`（junction 指到上面那份）

兩邊都是啟動時掃描 skill 目錄，**安裝後要重開 session** 才會看到。
skill 內的工作區路徑可用環境變數 `PPK_USE_HOME` 覆寫（預設 `C:\Working\agents\ppk_use`）。

## 快速開始

```bash
pip install -r requirements.txt          # 只有量測模式需要
python tools/ppk2_measure.py --check      # 確認儀器可連（只讀 VDD）
python tools/ppk2_measure.py --mv 3700 --power-on --seconds 60
```

多狀態量測與離線分析：

```bash
python tools/ppk2_measure.py --mv 3700 --power-on --seconds 0 --label session > temp/ppk2_session.log
python tools/ppk2_measure.py --avg temp/ppk2/<時間>_session.csv --start 12:47:20 --end 12:48:20
python tools/ppk2_measure.py --bursts temp/ppk2/<時間>_session.csv --threshold 1000 --gap 50
```

離線分析與測試不需要接儀器，也不需要第三方套件：

```bash
python -m unittest discover -s tests
```

細節（接線、避開反灌、電壓怎麼選、已知陷阱、歷史基準）看 `ppk.md`。
