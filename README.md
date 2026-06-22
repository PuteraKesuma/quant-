# ORB Research

Opening Range Breakout quantitative research — XAUUSD, NAS100, GBPUSD.

## Pipeline

```
1. Fetch raw 1m OHLCV → DuckDB
   # Dukascopy (default, deep history, no terminal needed)
   python -m pipeline.fetch.run_fetch --start 2020-01-01 --end 2026-06-09
   # MT5 (recent data, terminal must be open + logged in)
   python -m pipeline.fetch.run_fetch --source mt5 --start 2026-01-01 --end 2026-06-09

2. Audit data quality (integrity + continuity, auto-repair)
   python -m pipeline.audit.run_audit --once      # single pass
   python -m pipeline.audit.run_audit             # continuous worker

3. Build features → Parquet
   python -m pipeline.features.build_features

4. Backtest (in-sample)
   python -m pipeline.backtest.runner --start 2020-01-01 --end 2022-12-31

5. Forward test (out-of-sample fixed params)
   python -m pipeline.forward_test.runner --start 2023-01-01 --end 2023-12-31

6. Walk-forward optimization
   python -m pipeline.walk_forward.wfo_runner --start 2020-01-01 --end 2024-12-31

7. Reports
   python -m pipeline.analysis.report_gen --mode backtest

8. Live execution (FastAPI signal server + MQL5 EA)
   # start the signal server (the "brain")
   python -m pipeline.live.run_server
   # then in MetaTrader 5: compile mt5_ea/SignalExecutor.mq5, attach to a NAS100 chart.
   # one-time MT5 setup: Tools > Options > Expert Advisors >
   #   "Allow WebRequest for listed URL" > add http://127.0.0.1:8000
```

## Execution Layer

`pipeline/live/` is the decision server; `mt5_ea/SignalExecutor.mq5` is a
**strategy-agnostic** executor. The EA polls `GET /signals?symbol=NAS100` every
second and reconciles a position per strategy "slot" to the returned `action`
(BUY/SELL/FLAT). Each slot has its own `magic`, so multiple models can run
concurrently; a stable `signal_id` per magic makes it idempotent, so 1-second
polling never opens duplicate orders. Configure the active strategies (type
`dummy` or `orb`, plus future models) in `config.yaml` under `live.strategies`.

## Data

Raw data stored in `data/Level_0_Raw/*.duckdb` (gitignored).
Sources: **Dukascopy** (default, deep free history) and **MT5** (recent/live).
Symbol → instrument mapping is in `config.yaml`; fetchers are wired in `pipeline/fetch/registry.py`.

## Config

All strategy parameters in `config.yaml`.
