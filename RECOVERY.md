# VPS recovery / redeploy guide

This branch (`vps-zrev-live`) is the full live trading system. If the VPS is replaced,
everything here is recoverable from GitHub EXCEPT the few VPS-local secrets/data below.

## What IS in this repo (recoverable)
- `config.yaml` — live strategy slots (the deployed brain config).
- `pipeline/` — the FastAPI signal brain + strategies (`live/signal.py`), vision
  (`vision/`, incl. `tv_capture.py` TradingView capture), backtests (`backtest/`).
- `research/` — all audits/validation (reproducible).
- `_DOC/` — audit reports.
- `requirements.txt`, `mt5_ea/SignalExecutor.mq5`, `START_TRADING.bat`, `_MONITOR/` scripts.

## What is NOT in git (re-create on a new VPS)
- **`.env`** — must contain `ANTHROPIC_API_KEY=...` (required for the vision slot). NOT
  committed on purpose (secret).
- `data/Level_0_Raw/*.duckdb` — historical Dukascopy data (research only; live trading
  does NOT need it — the brain pulls live bars from MT5).
- MetaTrader 5 terminal + FBS-Demo login + the `SignalExecutor` EA attached to the
  XAUUSD and US100 charts with Algo Trading ON.
- `_MONITOR/jurnal.md` (contains account#/balance — recreated by the watchdog).

## Redeploy steps on a fresh VPS (Windows)
1. Install Python 3.11; `git clone` this repo (branch `vps-zrev-live`).
2. `pip install -r requirements.txt`
3. `python -m playwright install chromium` AND install the **MS VC++ 2015-2022 x64
   redistributable** (https://aka.ms/vs/17/release/vc_redist.x64.exe) — needed for the
   greenlet/Playwright vision TradingView capture.
4. Create `.env` with `ANTHROPIC_API_KEY=...`.
5. Install MT5, log into the FBS-Demo account, attach `mt5_ea/SignalExecutor.mq5` to
   the XAUUSD and US100 charts, enable "Algo Trading".
6. Run `START_TRADING.bat` (preflight + brain). Start `_MONITOR/watchdog_brain.ps1`.
   Add Startup-folder shortcuts + Windows auto-logon so it survives reboots. Close the
   VPS with RDP **Disconnect (X), never Sign out**.

## Live config summary (deployed)
- `zrev_xau` (magic 920622): always-in Donchian S&R XAU 1H, entry20/exit20, **H1 EMA100
  + Daily SMA50** trend gate, **z-score dynamic lot** (0.01-0.03), **atr_stop_mult 3.0**.
- `orb30_nas` (920617): NAS NY 30m ORB 1:1, DST open + SMA50 trend + 0.5R breakeven (fixed lot).
- `vision_smc_xau` (920621): Claude multi-TF SMC on real **TradingView** charts (demo only,
  not real-money validated). EA is strategy-agnostic and never modified.
