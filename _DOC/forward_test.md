# Forward test — proving the live edge before real money

**RESTARTED: 2026-07-03** (demo, FBS-Demo). Horizon: **~3 months (13 weeks)** from restart.
The 2026-07-01 test was voided: the book changed (Z archived, liquidity added, then the FULL
book chosen). The deployed book is now **Z (zrev 1H) + orb30_nas + liquidity_limit (15min)**
per `research/portfolio_best.py` (FULL book OOS-PF 1.87, net +$4685, 2022 ~breakeven; the
NAS+LIQ-only book was the weakest: OOS 1.56, negative 2022). Cone rebuilt from
`research/book_weekly.csv` (weekly $ dist of this exact book).
**Capital for eventual go-live:** $400, confirmed RISK CAPITAL (affordable to lose 100%).

## Why
Everything so far is **historical backtest** (2021-2026: IS/OOS, walk-forward, Monte-Carlo,
robustness, audit). The one validation we do **not** have is **forward / live-execution proof**
— that the backtest translates to real fills (spread, slippage, reconcile, the advisor). This
trial closes that gap at **zero money risk** (demo), then the go-live decision is data-driven.

## Go / no-go criteria (LOCKED 2026-07-01 — not to be moved later)
Track weekly with `python research/forward_tracker.py`.

| Criterion | Pass condition |
|---|---|
| P&L vs forecast | demo equity stays **inside the MC cone** (not below the 5th-pct floor) |
| Drawdown | realized maxDD **<= 30%** of start balance |
| System active | it is actually trading (roughly the expected trade rate) |
| Execution clean | no missed signals, no forced FLAT-close (review brain log + advisor journal) |
| **Your psychology** | you sit through a **red month without panic / without interfering** |

## Decision
- **~2-3 months, ALL green** -> consider go-live with the $400 risk capital, 0.01 lot, per the
  conservative plan (hard stop at -30%, never add money, never up-size to "catch up").
- **Any criterion fails** -> do NOT go live; we just saved real money and learned why.

## Guardrails during the trial
- Do NOT interfere with trades (interfering was proven to hurt the edge all session).
- Read the advisor (`advisor_journal.jsonl`) for confidence, but do not let it gate/override.
- After enough trades: `python research/advisor_eval.py` for the CAUTION-vs-outcome read.
