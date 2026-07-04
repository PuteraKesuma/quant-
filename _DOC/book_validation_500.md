# Book validation @ $500 — 2026-07-04

Deployed FULL book (Z zrev-1H + NAS ORB-stop + LIQ 15m) validated end-to-end with
`research/book_validation_500.py` (commit 2ef4428). $ at 0.01 lot = deployed size
(at $500 the capital-aware cap keeps Z at lot_min 0.01, so these $ are exact).
NOTE: the ORB series fills AT the range boundary — which live now ALSO does via
`orb_stop_manager` (pending STOP, deployed 2026-07-04). Slippage drag removed.

## (1) Full backtest 2021-2026

| | n | PF | IS | OOS | WR | net | maxDD |
|---|---|---|---|---|---|---|---|
| Z (zrev 1H) | 474 | 1.63 | 1.10 | 2.19 | 37% | +$2709 | −$313 |
| NAS (ORB) | 687 | 1.30 | 1.27 | 1.35 | 43% | +$612 | −$123 |
| LIQ (15m) | 525 | 1.33 | 1.15 | 1.67 | 41% | +$1364 | −$330 |
| **BOOK** | **1686** | **1.45** | **1.16** | **1.87** | **41%** | **+$4685** | **−$585** |

Per-year book net: 2021 **−$157**, 2022 −$1, 2023 +$370, 2024 +$782, 2025 +$1679,
2026H1 +$2013. **Profit is concentrated in 2024-2026** (gold trending regime).

## (2) Walk-forward (sequential 2-month OOS windows, fixed params)

23/33 windows green (70%); worst 2021-09 −$173; last 8 windows: 7 green (only
2025-07 −$88), PF 1.7-3.3.

## (3) Monte Carlo from $500 (weekly bootstrap, 4000 paths, iid ≈ 4-wk blocks)

| Horizon | median | 5% | 95% | P(hit −30% = $350) | P(end < $500) |
|---|---|---|---|---|---|
| 13w | ~$665 | ~$380 | ~$1220 | ~8.5% | ~19% |
| 26w | ~$885 | ~$427 | ~$1620 | ~11.5% | ~9% |
| 52w | ~$1290 | ~$585 | ~$2280 | ~14% | ~3% |

maxDD: median −$180/yr, p95 −$355.

## Honest read (the prediction)

- Central path: $500 → ~$665 in 3 months, ~$1290 in 1 year.
- BUT the weekly distribution is pulled by the fat 2024-2026 regime. If the regime
  reverts to 2021-2023, expect ~flat to +$100/yr. Regimes are not forecastable
  (ridge OOS R² negative). The honest forecast is the RANGE, not the median.
- ~1 in 7 one-year paths touches the −30% hard stop. Not a system failure —
  a consequence of $500 vs the book's −$585 historical maxDD. Locked rule: if it
  fires, stop; never add money; never up-size.
- Go-live remains gated on the 13-week demo forward test (started 2026-07-03
  @ $303.48; `research/forward_tracker.py` weekly).
