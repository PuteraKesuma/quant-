# Deploy ke VPS — Panduan "Tinggal Play"

Brain (Python) + Hands (MT5 EA) harus di **mesin yang sama** karena EA polling
`http://127.0.0.1:8000`. Jadi VPS-nya = **Windows VPS** yang menjalankan MT5 + brain
sekaligus.

> 💡 **Kabar baik:** live trading **tidak butuh data historis 1.9GB** (DVC). Bar live
> diambil langsung dari terminal MT5. Jadi cukup clone kode dari GitHub + MT5 — ringan.

---

## 1. Sewa Windows VPS
- Pilih **Forex VPS** (mis. ForexVPS, Cheap Forex VPS, FXVM) atau Windows VPS umum
  (Contabo, Vultr, AWS Lightsail Windows). RAM **≥ 2 GB**, Windows Server.
- Untuk latency order rendah: pilih lokasi VPS **dekat server broker FBS**.

## 2. Install prasyarat di VPS (sekali)
1. **Python 3.11+** — https://www.python.org/downloads/ → centang **"Add Python to PATH"**.
2. **MetaTrader 5** (dari FBS) → login akun → tombol **Algo Trading ON**.
3. **Git** (opsional, untuk clone) — https://git-scm.com/download/win.

## 3. Ambil kode dari GitHub
```cmd
git clone https://github.com/PuteraKesuma/quant-.git
cd quant-
```
(Atau download ZIP dari GitHub lalu extract.)

## 4. Jalankan SETUP (sekali)
Double-click **`SETUP.bat`** — otomatis:
- install semua dependency (`requirements.txt`),
- buat file `.env` dari template,
- jalankan pre-flight check.

## 5. Isi API key
Buka **`.env`**, tempel key kamu:
```
ANTHROPIC_API_KEY=sk-ant-api03-........
```
> `.env` TIDAK ikut GitHub (sengaja, demi keamanan) — jadi key diisi ulang di VPS.

## 6. Setup MT5 (sekali)
1. **Tools → Options → Expert Advisors** → centang *Allow WebRequest for listed URL*
   → tambah **`http://127.0.0.1:8000`**.
2. Pasang EA **SignalExecutor** ke chart **NAS100 (US100)** dan **XAUUSD**:
   - Set `ServerSymbol` / `TradeSymbol` per chart (NAS100→US100; XAUUSD→XAUUSD).
   - Pastikan ikon EA "smiley" 🙂 (Algo Trading ON).
3. **Simpan sebagai Profile/Template** supaya saat MT5 dibuka lagi, chart + EA langsung kepasang.

## 7. PLAY ▶️
Double-click **`START_TRADING.bat`** → pre-flight `READY` → brain jalan.
Biarkan jendelanya terbuka. Selesai.

---

## (Opsional) Auto-start saat VPS reboot
Supaya benar-benar "tinggal nyala":
1. **Brain auto-start:** sudah otomatis dipasang oleh `bootstrap_vps.ps1`. Mau pasang
   manual / ulang? double-click **`INSTALL_AUTOSTART.bat`** (bikin shortcut
   `START_TRADING.bat` di `shell:startup`). Saat Windows login, brain otomatis jalan.
2. **MT5 auto-login:** centang *Save account information* saat login; aktifkan *Auto
   arrange / load last profile*. EA ikut ke-load dari profile tersimpan (langkah 6.3).
3. Set VPS **auto-login Windows** (`netplwiz` → hapus centang "Users must enter a user
   name and password" → isi password) agar setelah reboot langsung masuk desktop dan
   auto-start ikut jalan.

## Update kode di VPS nanti
```cmd
cd quant-
git pull
SETUP.bat        REM kalau ada dependency baru
```

## Troubleshooting cepat
| Gejala | Penyebab / fix |
|--------|----------------|
| pre-flight `[FAIL] dep ...` | jalankan `SETUP.bat` lagi |
| pre-flight `MT5 belum konek` | buka MT5 & login dulu |
| EA log err **4014** | URL belum di-whitelist (langkah 6.1) |
| EA log err **5203** | brain belum jalan / baru restart (auto-pulih) |
| vision FLAT terus | normal di luar `active_windows_utc`, atau guard RR/confidence |
