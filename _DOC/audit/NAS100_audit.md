# NAS100 — Data Audit

- Rows: **2,198,910**
- Range (UTC): 2019-12-31 16:00:00+00:00 → 2026-06-08 15:55:00+00:00
- Daily break (auto-detected): (1, 62.0)
- Gaps: 1751 total | intraday anomalies: 52 | missing bars: 507
- Repair: refetched=0, filled(synthetic)=395, anomalies 254→52

## Integrity

| check | severity | count |
|---|---|---|
| flatline | warning | 194 |

## Gap classification

| class | count |
|---|---|
| ANOMALY_INTRADAY | 52 |
| EXPECTED_BREAK | 1,292 |
| EXPECTED_DAILY_BREAK | 16 |
| EXPECTED_HOLIDAY | 55 |
| EXPECTED_WEEKEND | 336 |

## Completeness by year

| year | bars | missing | completeness % |
|---|---|---|---|
| 2019 | 315 | 0 | 100.0 |
| 2020 | 341,154 | 141 | 99.9587 |
| 2021 | 343,927 | 20 | 99.9942 |
| 2022 | 342,800 | 0 | 100.0 |
| 2023 | 340,982 | 33 | 99.9903 |
| 2024 | 342,276 | 209 | 99.939 |
| 2025 | 340,614 | 48 | 99.9859 |
| 2026 | 147,237 | 56 | 99.962 |
