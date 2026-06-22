# XAUUSD — Data Audit

- Rows: **2,280,815**
- Range (UTC): 2019-12-31 16:00:00+00:00 → 2026-06-08 16:00:00+00:00
- Daily break (auto-detected): (20, 61.0)
- Gaps: 1776 total | intraday anomalies: 10 | missing bars: 103
- Repair: refetched=0, filled(synthetic)=0, anomalies 10→10

## Integrity

| check | severity | count |
|---|---|---|
| flatline | warning | 3,466 |

## Gap classification

| class | count |
|---|---|
| ANOMALY_INTRADAY | 10 |
| EXPECTED_BREAK | 2 |
| EXPECTED_DAILY_BREAK | 1,382 |
| EXPECTED_HOLIDAY | 46 |
| EXPECTED_WEEKEND | 336 |

## Completeness by year

| year | bars | missing | completeness % |
|---|---|---|---|
| 2019 | 360 | 0 | 100.0 |
| 2020 | 355,553 | 19 | 99.9947 |
| 2021 | 354,376 | 0 | 100.0 |
| 2022 | 354,667 | 0 | 100.0 |
| 2023 | 353,126 | 18 | 99.9949 |
| 2024 | 355,825 | 20 | 99.9944 |
| 2025 | 354,496 | 39 | 99.989 |
| 2026 | 152,412 | 7 | 99.9954 |
