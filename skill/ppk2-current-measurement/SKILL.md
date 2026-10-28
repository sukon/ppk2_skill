---
name: "ppk2-current-measurement"
description: "Measure device current consumption with a Nordic PPK2 power profiler: idle/RX/TX current, multi-state sessions, offline window averages and current-burst analysis. Use when the user says 量電流 / 待機電流 / 耗電 / 電流量測 / 峰值電流, mentions PPK2, Power Profiler, current consumption, average current, or wants a 1ms current trace recorded. Tool, manual and offline analysis live in C:\\Working\\agents\\ppk_use (override with PPK_USE_HOME)."
version: 3
created: "2026-10-28"
updated: "2026-10-28"
---
## When to Use
Use for any task that measures or analyses current consumption of an embedded device with a Nordic PPK2 (source-meter mode): idle/standby average current, continuous-RX current, TX peak current and duration, before/after firmware power comparisons, multi-state sessions, or re-analysing an existing 1ms current CSV. Also use when the user mentions PPK2, Power Profiler, 量電流, 待機電流, 耗電, 電流量測, 峰值電流. Do NOT use for bench supply measurements without a PPK2, or for energy profiling of code paths that need a logic analyzer instead.

## Procedure
1. Resolve the workspace once: PPK_USE_HOME (default C:\Working\agents\ppk_use). Everything else uses that one path; do not hardcode other copies. Read `<workspace>`/ppk.md for the full manual (wiring, voltage safety, procedures, pitfalls) and `<workspace>`/TOOLS.md for exact arguments.
2. Check prerequisites before touching hardware: is the PPK2 plugged into this PC (look for its COM port) and is nRF Connect Power Profiler closed? Only one program may hold the PPK2 at a time. If the PPK2 is not connected, you can still do offline analysis (--avg / --bursts) and the offline test suite; say plainly that hardware behaviour is unverified.
3. Pick the DUT supply voltage from the device's DOCUMENTED input-voltage limits first (datasheet / hardware spec: allowed input range, regulator dropout, absolute maximum). Battery chemistry is only a hint, never the safety basis (Li-ion 3.7V nominal / 4.2V full, LiFePO4 3.65V, NiMH 1.2V). The tool only accepts 3000-4200mV by default and aborts (never silently clamps) outside that; widen it with --mv-range only when the documented limits allow it. If the device documentation is unavailable, ask the user instead of guessing - a wrong voltage can destroy the DUT.
4. Eliminate backfeed: unplug the DUT's USB / charger / debug-probe power before measuring, and confirm no other supply path reaches the battery terminal. Peripheral current drawn from USB will not appear in the reading.
5. Confirm the instrument: python `<workspace>`/tools/ppk2_measure.py --check (reads back VDD, sends no settings - the tool's --check path is read-only). Then confirm the voltage: set it, read it back, abort on mismatch (the tool already does this; --power-on re-checks once more and refuses to switch power on when VDD cannot be read).
6. Use a release (non-debug) firmware build: debug builds often stay awake or hold debug pins, which inflates standby current.
7. Measure one state at a time: python `<workspace>`/tools/ppk2_measure.py --mv `<MV>` [--power-on] --seconds 60. Let the state settle first (post-advertise / post-handshake transients are not steady state).
8. For several states, do NOT power-cycle between them: closing the serial port switches the PPK2's DUT output off, and many devices only boot from a button press after power loss. Run one continuous session instead: --mv `<MV>` --power-on --seconds 0 --label session > temp/ppk2_session.log, switch states while it records (note each switch's wall-clock time), then Ctrl-C to stop.
9. Analyse offline (no instrument needed): --avg CSV --start HH:MM:SS --end HH:MM:SS for each state window (inclusive bounds, sub-second timestamps allowed), and --bursts CSV --threshold `<uA>` --gap `<ms>` to list current bursts.
10. Interpret honestly: --bursts reports threshold crossings on 1ms averages, i.e. candidate current bursts - not instantaneous peaks and not necessarily TX. Sub-millisecond spikes are averaged away, so reported peaks are lower bounds.
11. Record the result set: supply voltage and the documented limit it was chosen from, firmware version, device state, time window, average/min/max, and where the CSV lives (temp/ppk2/). Compare against a baseline only if the baseline is the same hardware/firmware/states; otherwise start a new baseline.
## Pitfalls
- Only one program may connect to the PPK2. Close nRF Connect Power Profiler first; if it was closed abruptly, binary sample data may remain in the serial buffer - the tool stops measuring and flushes the buffer before reading metadata.
- Closing the serial port switches the PPK2's DUT output off (observed in practice). Multi-state measurements therefore need one --seconds 0 session plus --avg windows, not repeated power cycles.
- Some devices do not power on by themselves after power loss (e.g. LG-M202 needs the power button). Another reason to use a single session.
- Voltage must be read back from PPK2 metadata and compared with the requested value; on mismatch the tool aborts, and --power-on refuses to switch power on when VDD cannot be read.
- Windows keeps a paired BLE device connected even when the phone is out of range - "disconnected" needs the platform's own tool (LG-M202: ble_probe.py bt-off). After a real disconnect some firmwares advertise fast for the first ~30s; skip that window.
- 1ms averaging hides sub-millisecond peaks; the reported peak is a lower bound. Device-specific pitfalls (power button, advertising delays, CAD interval effects) belong in the device's own repo, not in this skill.
- Historical baselines in the manual are device- and date-specific (LG-M202, 2026-10-07). Never present them as general values.

## Verification
1. python -m unittest discover -s tests (from the workspace) passes - covers CSV parsing, window boundaries, midnight rollover, burst merging and interrupted-capture cleanup, and needs no hardware.
2. python `<workspace>`/tools/ppk2_measure.py --check prints the current VDD (proves instrument connection and metadata readback); it is read-only and sends no supply or mode settings.
3. Open-circuit floor (measured on this PC, 2026-10-28, no DUT attached): output off ~0.1uA, output on at 3.7V ~41.5uA steady. Readings below ~42uA are instrument leakage/offset, not device current - treat them as noise floor.
4. After a run, the CSV exists under `<workspace>`/temp/ppk2/ and its first line is 'time,uA'; the tool prints average/min/max plus the CSV path.
5. Offline re-analysis of that CSV with --avg over a known window reproduces the expected average, and --bursts lists bursts whose timestamps match the recorded state switches.
6. When hardware was not connected, state explicitly that hardware behaviour is unverified in this workspace (the documented behaviour is inherited from lg_mesh's 2026-10 measurements).