# PRD Master — ORB Live Trading System

**Proyek:** ORB Research / Quant
**Komponen:** Execution Layer (brain/hands) + Strategy Slots — 3 capability: rule-based (ORB) · AI-vision · machine learning
**Versi:** 4.0 — three-capability (rule-based · vision · ML)
**Status:** ✅ Server & EA live · ✅ ORB live (NAS100 + XAU) · 🧪 Vision selesai dikoding (belum live) · 🔜 ML direncanakan (§6.3, §10)
**Update:** 2026-06-22

---

## 1. Tujuan & Arsitektur

Memisahkan **otak** (keputusan di Python) dari **tangan** (eksekusi di MetaTrader 5), sesuai `_DOC/Arsitekur_EA.png`. Otak boleh menjalankan banyak model sekaligus; EA tetap **strategy-agnostic** — ia hanya mengeksekusi apa pun yang server perintahkan, tanpa tahu strateginya.

Sistem dirancang untuk **3 capability** yang berbagi **kontrak `SignalResponse` & EA yang sama** — beda capability cukup beda `type` di registry:
1. **Rule-based / ORB** — ✅ live (NAS100 + XAU).
2. **AI-vision** (screenshot → Claude/SMC → signal) — 🧪 siap, belum live.
3. **Machine Learning** (XGBoost · Random Forest · LSTM) — 🔜 rencana (spec §6.3, roadmap §10).

```
LOCAL HOST — FastAPI (otak)        MetaTrader 5 — EA (tangan)
┌───────────────────────┐  GET /signals (poll 1 dtk)  ┌──────────────────┐
│ Data │ Model │ Signal  │ ◄────────────────────────── │  SignalExecutor  │
│                        │ ──── JSON list ───────────► │  (eksekusi order)│
└───────────────────────┘                             └──────────────────┘
```

### 1.1 Arsitektur Runtime (lengkap)

Satu **server** (1 proses) memegang banyak **slot** independen (magic masing-masing). Tiap chart MT5 menjalankan satu **EA** yang menjalankan **semua** sinyal di list-nya — bukan memilih salah satu.

```
              ┌──────────────────── BRAIN — FastAPI (1 proses) ────────────────────┐
              │  SignalEngine · registry STRATEGY_TYPES {dummy, orb, vision}        │
              │                                                                     │
              │   slot orb30_nas   (NAS100, magic 920617) ─┐                        │
              │   slot orb30_xau   (XAUUSD, magic 920618) ─┤ tiap slot: state +     │
              │   slot vision_xau  (XAUUSD, magic 920619) ─┘ posisi sendiri         │
              │                                                                     │
              │   DataProvider ──bar M1── MT5     vision: capture→PNG→Claude API    │
              └──────▲───────────────────────────────────────────────▲────────────┘
        GET /signals?symbol=NAS100                      GET /signals?symbol=XAUUSD
        evaluasi slot NAS100 (berurutan)                evaluasi slot XAUUSD (berurutan)
        balas list:[orb30_nas]                          balas list:[orb30_xau, vision_xau]
                     │                                                  │
                     ▼ (poll 1 dtk)                                     ▼ (poll 1 dtk)
   ┌──────────────── MT5 — EA (tangan) ────────────────────────────────────────────┐
   │  EA #1  chart US100                         EA #2  chart XAUUSD                  │
   │  ServerSymbol=NAS100 / Trade=US100          ServerSymbol=XAUUSD / Trade=XAUUSD  │
   │  loop list → reconcile per magic            loop list → reconcile per magic     │
   │    └ magic 920617 → posisi US100              ├ magic 920618 → posisi XAU (ORB)  │
   │                                               └ magic 920619 → posisi XAU (AI)  │
   └──────┬─────────────────────────────────────────────────┬───────────────────────┘
          │ order + SL/TP                                    │ order + SL/TP
          ▼                                                  ▼
   ┌──────────────────────────── BROKER (FBS, demo) ─────────────────────────────────┐
   │  posisi independen per magic · exit otomatis lewat SL/TP                          │
   └──────────────────────────────────────────────────────────────────────────────────┘
```

**Cara membaca:**
- **Otak = 1 proses.** Per request `/signals?symbol=X`, server mengevaluasi **semua slot bersimbol X secara berurutan** lalu membalas satu list. Request **beda simbol** dilayani di thread terpisah → benar-benar paralel (XAU tidak menunggu NAS).
- **Tangan = 1 EA per chart.** EA mem-parse seluruh list dan mengelola **satu posisi per `magic`** sekaligus — jadi banyak model = banyak posisi **paralel**, bukan balapan "siapa duluan". Beda simbol = EA terpisah (karena `ServerSymbol`/`TradeSymbol` beda).
- **Idempotency** menjaga polling 1 dtk tidak dobel order (lihat §2): EA hanya bertindak saat `signal_id` per `magic` berubah.
- **Cadence vision:** Claude dipanggil ~1×/`interval_minutes`; poll lain dilayani cache instan. Hanya saat panggilan API itu (beberapa detik) slot lain bersimbol sama menunggu satu siklus — alasan interval dibuat 15 menit, bukan per detik.
- **Tambah model** = slot + `magic` baru di config; EA tidak pernah berubah.

---

## 2. Desain Inti — Idempotency & Multi-Model

Polling 1 detik berarti server bisa membalas sinyal yang sama puluhan kali. Anti-duplikat dengan **desired-state + signal_id**, bukan perintah sekali jalan:

- **`action`** = state yang HARUS dipegang: `BUY` (long), `SELL` (short), `FLAT` (tidak ada posisi).
- **`signal_id`** = penanda unik & **stabil** selama sinyal aktif. EA menyimpan `signal_id` terakhir **per `magic`** dan hanya bertindak saat berubah → polling 1 dtk tak pernah dobel order.
- **`magic` unik per slot** → tiap model memegang posisinya sendiri tanpa tabrakan (multi-model).
- **Exit oleh broker** lewat SL/TP yang dikirim saat order dibuka.
- **Pluggable**: tambah model = tambah class di registry `STRATEGY_TYPES` + slot config baru. **EA tidak pernah diubah.**

---

## 3. Kontrak API

`GET /health` → `{status, uptime_seconds, strategies:[{name,type,symbol}], ea:{...}}`

`GET /signals?symbol=NAS100` → **list**, satu item per slot:
```json
{ "symbol":"NAS100", "ts":"...Z", "signals":[
  {"strategy":"orb30_nas","symbol":"NAS100","action":"BUY",
   "sl":30189.03,"tp":30249.47,"lot":0.01,"magic":920617,
   "signal_id":"NAS100-orb30_nas-20260622-new_york-LONG","ts":"...Z"}
]}
```
`action` ∈ `BUY|SELL|FLAT`. Kontrak ini **tetap** — semua model memakai bentuk yang sama.

---

## 4. Arsitektur File

| File / Folder | Peran |
|---|---|
| `pipeline/live/contracts.py` | Skema `SignalResponse` + `SignalSet` (Pydantic) — **jangan diubah** |
| `pipeline/live/data.py` | `DataProvider` — bar M1 live dari MT5 |
| `pipeline/live/signal.py` | `SignalEngine` + registry `STRATEGY_TYPES` (`dummy`, `orb`, `vision`) |
| `pipeline/live/server.py` | Endpoint FastAPI `/health`, `/signals` |
| `pipeline/live/run_server.py` | Entrypoint uvicorn |
| `pipeline/vision/` | `capture` · `analyzer` · `state` · `journal` · `prompt.md` (slot AI-vision) |
| `pipeline/features/` + `model/` | Feature engineering & artefak model — dipakai capability ML (train & live) |
| `pipeline/train/` *(rencana)* | Pipeline training ML (XGB/RF/LSTM) → simpan artefak ke `model/` |
| `mt5_ea/SignalExecutor.mq5` | EA generic — parse list, kelola posisi per `magic` |
| `config.yaml` → `live.strategies` | Daftar slot strategi aktif |

---

## 5. Konfigurasi (`config.yaml`)

```yaml
live:
  host: "127.0.0.1"
  port: 8000
  data_source: mt5
  recent_bars: 600
  strategies:                # tiap entry = 1 slot independen, magic WAJIB unik
    - { name: orb30_nas, type: orb,    symbol: NAS100, magic: 920617, ... }
    - { name: orb30_xau, type: orb,    symbol: XAUUSD, magic: 920618, ... }
    - { name: vision_xau, type: vision, symbol: XAUUSD, magic: 920619, ... }

vision:                      # audit trail slot AI-vision
  journal_path: "vision_journal.jsonl"
  archive_dir: "_DOC/vision"
```
Matikan model = hapus/comment slot-nya; sistem kembali rule-based murni tanpa ubah kode.

---

## 6. Strategy Slots

| Slot | Type | Simbol | Magic | Ringkas |
|---|---|---|---|---|
| `orb30_nas` | orb | NAS100 (US100) | 920617 | ORB-30 NY + range-filter, RR 1:1 — edge rank-1 |
| `orb30_xau` | orb | XAUUSD | 920618 | ORB-30 NY, RR 1:3, pakai SL |
| `vision_xau` | vision | XAUUSD | 920619 | AI-vision SMC (Claude) — 🧪 belum live |
| `ml_*` *(rencana)* | ml | XAU / NAS | 9206xx | XGBoost · Random Forest · LSTM — capability ke-3, belum dikoding |

### 6.1 ORB (rule-based)
Range = N menit pertama sesi (NY 13:30 UTC). Break atas → LONG, break bawah → SHORT (simetris, 1 trade/sesi). SL/TP = kelipatan ukuran range; time-exit di `session_end_utc`.
- **`orb30_nas`** — range 30m, RR 1:1 (`use_sl`), **range-filter** (hanya trade kalau range 0.5–1.5× median 20-hari). Backtest PF 1.13→1.28 (IS→OOS), win 56%. **Walk-forward tervalidasi** (8/10 fold OOS profit). Lot 0.01.
- **`orb30_xau`** — range 30m, RR 1:3 (`tp_mult:3, sl_mult:1`, `use_sl`). Backtest PF ~1.20. Lot 0.01.
- **Faithful-exit**: server kirim `FLAT` begitu harga live menyentuh SL/TP (sesuai backtest) → mencegah re-entry & error "invalid stops" saat harga sudah whipsaw lewat SL.

### 6.2 Vision (AI / Smart Money Concepts)
Slot `type: vision` menganalisa **screenshot chart** lewat Claude API dan menghasilkan `SignalResponse` yang identik dengan ORB. Alur `VisionStrategy.evaluate()`:

```
cadence gate (cache jika belum waktunya)
  → capture (render chart MT5 via mplfinance → PNG)
  → analyze (PNG + prompt SMC → Claude → JSON: action/confidence/sl/tp/reason)
  → guards (confidence < min_conf  atau  RR < min_rr  → FLAT)
  → commit (signal_id naik HANYA jika action berubah → EA idempoten)
  → journal (catat keputusan + arsip screenshot)
```

Prinsip:
- **Cadence**: Claude dipanggil maksimal 1×/`interval_minutes` (default 15m); poll di antaranya melayani cache → signal_id stabil → EA tidak buka order baru.
- **Fail-safe**: API down / timeout / output rusak → slot balas **cache atau FLAT**. Server **tidak pernah 500**, slot ORB tidak terganggu.
- **Capture**: default `mt5` (render sendiri, harga = broker FBS). Mode `tradingview` (Playwright) opsional + butuh `price_offset`.
- Parameter slot: `interval_minutes, capture_mode, chart_timeframe, chart_bars, model (claude-opus-4-8), min_confidence, min_rr, price_offset, prompt_file`.

### 6.3 Machine Learning (capability ke-3 — 🔜 RENCANA, belum dikoding)

**Arsitektur sudah mendukung.** ML masuk sebagai `type: ml` di `STRATEGY_TYPES`, keluaran `SignalResponse` **identik** — `server.py`, `contracts.py`, `data.py`, dan **EA tidak berubah** (persis pola vision). Boleh lebih dari satu slot (mis. `ml_xgb_xau`, `ml_lstm_nas`), masing-masing magic sendiri.

Ada **dua sisi** yang perlu dibangun: training (offline) dan inference (live).

**A. Sisi training (offline) — `pipeline/train/` (baru)**
```
Level_0 (DuckDB OHLCV)
  → pipeline/features/build_features.py    fitur: return, ATR, RSI, EMA, ukuran range, jam sesi, dst → Level_1_Features
  → labeling                               target: arah naik/turun dalam H bar (klasifikasi) atau triple-barrier → Level_2_Datamart
  → train  (XGBoost / RandomForest / LSTM) split WALK-FORWARD kronologis (bukan acak) — hindari look-ahead
  → simpan artefak → model/<symbol>/<nama>.{pkl|joblib|keras}
  → validasi → pipeline/backtest + pipeline/walk_forward  (PF, expectancy, OOS, setelah spread)
```

**B. Sisi inference (live) — `MLStrategy(BaseStrategy)` di `signal.py`**
```
evaluate():
  → ambil bar live (DataProvider)
  → hitung fitur dengan KODE YANG SAMA seperti training (build_features)  ← feature parity wajib
  → load artefak sekali (cache), model.predict_proba()
  → map: prob ≥ threshold → BUY/SELL, selain itu FLAT
  → sl/tp dari policy (ATR-based atau RR tetap)        ← model hanya beri arah; sizing terpisah
  → decide hanya per CLOSED bar (anti look-ahead)
  → SignalResponse identik (signal_id naik saat action berubah → EA idempoten)
```

Parameter slot (rencana): `model_file, model_kind (xgb|rf|lstm), feature_set, threshold_long, threshold_short, horizon_bars, sl_atr_mult, tp_atr_mult, decide_on: closed_bar`.

**3 hal kritis (penyebab umum ML-trading gagal):**
1. **Feature parity train↔live** — fitur live HARUS dihitung pakai modul yang sama (`pipeline/features`). Beda sedikit = train/serving skew → bagus di backtest, jeblok live.
2. **No look-ahead / leakage** — split walk-forward kronologis (sudah ada `pipeline/walk_forward`); label tidak boleh bocor ke fitur; putuskan hanya di bar yang sudah close.
3. **Model ≠ sizing** — ML prediksi arah/probabilitas; `sl`/`tp` tetap wajib di kontrak → policy terpisah (ATR/RR). Validasi **net setelah spread** sebelum dipercaya.

**Dependencies** (tambah saat fase ini, lazy-import seperti `anthropic`/`mplfinance` di vision agar server tetap ringan): `xgboost`; `tensorflow`/`keras` atau `torch` (LSTM). `scikit-learn` (Random Forest) **sudah ada**.

---

## 7. Status

**Execution layer — live & teruji:**
- ✅ `/health` & `/signals` jalan; idempotency terverifikasi (signal_id stabil dalam 1 sinyal).
- ✅ EA compile 0 error; konek HTTP 200; order tereksekusi dengan magic benar; tidak ada dobel order.
- ✅ Pemisahan simbol: server `NAS100` → broker `US100`; EA punya guard tolak SL/TP sisi salah.

**Strategi live:**
- ✅ `orb30_nas` (NAS100) — edge rank-1, walk-forward tervalidasi, lot 0.01.
- ✅ `orb30_xau` (XAUUSD) — lot 0.01.

**Vision — selesai dikoding, BELUM live:**
- ✅ Modul `pipeline/vision/*` + `VisionStrategy` + slot `vision_xau` di config.
- ✅ 19 unit/integration test lulus; smoke test server OK (fail-safe & happy path).
- ⏳ Aktivasi: isi `ANTHROPIC_API_KEY` di `.env` → restart brain. Saat live, magic 920619 jalan **berdampingan** dengan orb30_xau (920618) = perbandingan langsung rule-based vs AI di akun demo.

---

## 8. Cara Pakai

1. **Server**: `python -m pipeline.live.run_server` (atau `start_brain.bat`) → cek `http://127.0.0.1:8000/health`.
2. **Whitelist URL** (sekali): MT5 → Tools → Options → Expert Advisors → Allow WebRequest → `http://127.0.0.1:8000`.
3. **EA**: compile `SignalExecutor.mq5` (0 error), pasang di chart, **Algo Trading ON**. Set `ServerSymbol` (kunci riset, mis. NAS100) & `TradeSymbol` (simbol broker, mis. US100) per instance.
4. **Tambah model**: class baru → daftar di `STRATEGY_TYPES` → slot config `magic` baru. EA tetap.

---

## 9. Risiko & Backlog

**Risiko:** `magic` wajib unik (kalau sama, posisi tabrakan) · EA & server harus satu mesin (localhost) · selalu uji di **demo**, `MaxLot` sebagai pengaman · backtest belum modelkan spread (PF = batas atas optimistis).

**Backlog:** scale lot NAS100 ke 0.02–0.03 setelah konfirmasi · backtest dengan spread · log order EA → file/DB untuk rekonsiliasi expected vs actual · ensemble beberapa model jadi satu keputusan · forward-test vision 1 jam di demo (pantau journal, posisi tak ganda, call AI ~1×/15m) · **bangun capability ML (§10).**

---

## 10. Roadmap Machine Learning (capability ke-3)

Pengembangan ML dilakukan bertahap; pekerjaan riilnya ada di **pipeline training + feature parity + sizing policy**, bukan di plumbing eksekusi (yang sudah beres lewat registry).

| Fase | Isi | Status |
|---|---|---|
| **0 — Fondasi** | `model/`, `pipeline/features/`, `Level_1/Level_2`, `pipeline/backtest`, `pipeline/walk_forward`, `scikit-learn` | ✅ sudah ada |
| **1 — Data & fitur** | Tentukan `feature_set` + skema labeling (arah H-bar / triple-barrier); bangun dataset `Level_2` per symbol | 🔜 |
| **2 — Training** | `pipeline/train/` — mulai RF/XGBoost (cepat, tabular); simpan artefak ke `model/`; validasi walk-forward (target PF>1 & expectancy>0 **OOS setelah spread**) | 🔜 |
| **3 — Inference** | `MLStrategy` + daftar `"ml"` di `STRATEGY_TYPES`; reuse `build_features` (feature parity); sl/tp policy (ATR/RR) | 🔜 |
| **4 — Shadow → live** | Slot `ml_*` magic baru, jalan **berdampingan** ORB + vision di demo; bandingkan 3 capability lewat journal/log; naikkan lot hanya bila OOS konsisten | 🔜 |
| **5 — Lanjutan** | LSTM (sekuens harga); **ensemble/meta-model** menggabung sinyal ORB + vision + ML jadi satu keputusan net | 🔜 |

**Keputusan yang masih perlu ditentukan** (menentukan label & fitur sebelum Fase 1 jalan): target = **klasifikasi arah** vs **regresi return**; **horizon** (berapa bar ke depan); **symbol** pertama (XAU / NAS100); **timeframe** dasar (M5/M15/H1).
