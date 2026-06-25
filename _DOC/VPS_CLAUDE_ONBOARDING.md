# Pasang Claude Code di VPS + Prompt Pembuka

Tujuan: di VPS bisa jalan **Claude Code** sendiri, supaya kalau ada masalah
(brain mati, EA error, dll) Claude di VPS bisa lihat log & benerin langsung —
tanpa kamu harus copy-paste bolak-balik dari laptop.

---

## A. Install Claude Code di VPS (sekali)

Buka **PowerShell sebagai Administrator** di VPS, lalu:

### Cara 1 — installer native (paling gampang, TIDAK butuh Node.js)
```powershell
irm https://claude.ai/install.ps1 | iex
```
Tutup PowerShell, buka lagi (biar PATH ke-refresh), cek:
```powershell
claude --version
```

### Cara 2 — via npm (kalau Cara 1 gagal; butuh Node.js 18+)
```powershell
# install Node dulu kalau belum ada: https://nodejs.org (LTS)
npm install -g @anthropic-ai/claude-code
claude --version
```

## B. Login (pakai akun langgananmu)
```powershell
cd C:\Quant
claude
```
Di dalam Claude Code ketik:
```
/login
```
Browser di VPS akan terbuka → login akun Claude kamu → balik ke terminal, sudah masuk.

> Catatan: ini login **langganan Claude Code** (untuk Claude yang ngoding di VPS).
> BEDA dengan `ANTHROPIC_API_KEY` di file `.env` — yang itu khusus dipakai slot
> **vision** untuk analisa chart. Dua-duanya perlu, jangan tertukar.

## C. Mulai kerja
Selalu jalankan dari folder kode supaya `CLAUDE.md` ke-load otomatis:
```powershell
cd C:\Quant
claude
```
Lalu tempel **Prompt Pembuka** di bawah ini sebagai pesan pertama.

---

## D. PROMPT PEMBUKA (copy-paste ke Claude Code baru di VPS)

```
Kamu jalan DI DALAM VPS produksi yang sedang LIVE TRADING. Folder kode: C:\Quant.

Baca CLAUDE.md dulu — itu konteks utama proyek (riset ORB + execution layer
brain/hands). Ringkas yang WAJIB kamu tahu sebelum mengubah apa pun:

ARSITEKTUR
- "Brain" = FastAPI server Python (pipeline/live/), memutuskan sinyal.
- "Hands" = EA MQL5 (mt5_ea/SignalExecutor.mq5) di MT5, cuma eksekusi order.
- EA polling GET /signals tiap 1 detik; server balas desired-state (BUY/SELL/FLAT)
  + signal_id. EA bertindak hanya saat signal_id berubah. Idempoten.
- Brain dijalankan lewat START_TRADING.bat (jendela harus tetap terbuka).
  Auto-start sudah dipasang (shortcut di shell:startup).

SLOT YANG LIVE (config.yaml -> live.strategies), dikenali via Magic:
- 920617 orb30_nas      (NAS100/US100, ORB sesi NY)        — rule-based
- 920618 orb30_xau      (XAUUSD, ORB sesi NY)              — rule-based
- 920620 orb30_xau_asia (XAUUSD, ORB sesi Asia 00:00 UTC)  — rule-based
- 920621 vision_smc_xau (XAUUSD, AI multi-TF H1/M15/M5)    — panggil Claude vision

ATURAN PENTING (jangan dilanggar):
1. JANGAN ubah logika ORB (pipeline/backtest/strategy_orb.py & param ORB) —
   itu sudah divalidasi backtest. Boleh ubah hanya wrapper live kalau perlu.
2. File .env berisi API key ASLI (ANTHROPIC_API_KEY=sk-ant-...). JANGAN commit,
   JANGAN push, JANGAN tampilkan isinya. .env sudah di-gitignore.
3. Semua waktu di kode = UTC. TAPI MT5 FBS mengirim waktu bar & deal dalam waktu
   server broker (UTC+3 musim panas / UTC+2 musim dingin). data.py sudah otomatis
   menggeser ke UTC (live.mt5_server_utc_offset_hours: null = auto-detect).
4. SL/TP disimpan di broker — posisi terbuka tetap aman walau brain mati.
   Breakeven dijalankan EA-side (butuh MT5 hidup). Lock-profit reversal & sinyal
   baru butuh brain hidup.

CARA CEK SEHAT / DEBUG
- Brain hidup? Jendela START_TRADING terbuka + buka http://127.0.0.1:8000/health.
- Log keputusan vision + alasan: file vision_journal.jsonl (append-only).
  Screenshot yang dilihat Claude diarsipkan ke _DOC/vision/ HANYA saat aksi BERUBAH.
- Slot ORB TIDAK pernah screenshot — jadi "SELL tanpa screenshot" = normal (ORB),
  bukan vision.
- Tes: `python -m pytest tests/ -q` (harus hijau sebelum & sesudah perubahan).
- Update kode dari GitHub: jalankan ulang bootstrap (irm .../bootstrap_vps.ps1 | iex)
  ATAU `git pull`. Bootstrap stop brain dulu, robocopy /MIR, pertahankan .env.

GAYA KERJA
- Sebelum ubah apa pun yang menyentuh order/uang, jelaskan dulu rencananya singkat.
- Jangan push ke git kecuali aku minta. Kalau push, jangan pernah ikutkan .env.
- Akun MT5 di VPS ini terpisah dari laptop — jangan asumsikan sama.

Tugas pertamamu: baca CLAUDE.md + config.yaml, lalu lapor status: brain jalan/tidak,
MT5 konek/tidak, ada posisi terbuka apa (magic berapa), dan ada error di log tidak.
```

---

## E. Tips
- Kalau Claude di VPS perlu jalankan perintah lama (server), pakai jendela terpisah
  supaya sesi Claude tidak ke-block.
- Jangan tutup RDP dengan "Sign out" — pakai tombol ✕ (Disconnect) biar brain & MT5
  tetap jalan. (Lihat juga: cara nutup VPS yang benar.)
- Satu Claude Code di VPS sudah cukup. Tidak perlu yang di laptop kalau kamu sudah
  kerja langsung di VPS.
