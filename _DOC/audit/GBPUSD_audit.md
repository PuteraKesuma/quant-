# GBPUSD — Data Audit

- Rows: **2,394,377**
- Range (UTC): 2019-12-31 16:00:00+00:00 → 2026-06-08 16:00:00+00:00
- Daily break (auto-detected): (22, 50.0)
- Gaps: 6280 total | intraday anomalies: 48 | missing bars: 508
- Repair: refetched=0, filled(synthetic)=2,590, anomalies 2070→48

## Integrity

| check | severity | count |
|---|---|---|
| flatline | warning | 28,271 |

## Gap classification

| class | count |
|---|---|
| ANOMALY_INTRADAY | 48 |
| EXPECTED_BREAK | 2 |
| EXPECTED_DAILY_BREAK | 5,888 |
| EXPECTED_HOLIDAY | 6 |
| EXPECTED_WEEKEND | 336 |

## Completeness by year

| year | bars | missing | completeness % |
|---|---|---|---|
| 2019 | 360 | 0 | 100.0 |
| 2020 | 374,332 | 56 | 99.985 |
| 2021 | 372,194 | 0 | 100.0 |
| 2022 | 372,775 | 14 | 99.9962 |
| 2023 | 372,300 | 134 | 99.964 |
| 2024 | 373,195 | 188 | 99.9496 |
| 2025 | 371,506 | 116 | 99.9688 |
| 2026 | 160,305 | 0 | 100.0 |
