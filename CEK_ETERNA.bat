@echo off
setlocal
title CEK ETERNA - apa yang sedang ditunggu
cd /d "%~dp0"

echo.
echo ============================================================
echo    ETERNA - dia jalan atau tidak, dan sedang menunggu apa
echo ============================================================
echo.
echo  Kalau ETERNA tertulis FLAT, itu BUKAN berarti rusak. Eterna
echo  hanya masuk PADA SAAT flip Supertrend yang searah gate tren.
echo  Di luar momen flip itu dia memang diam. Rata-rata sekitar
echo  2 sinyal per minggu, dan sekitar separuhnya dibatalkan zrev.
echo.

python _MONITOR\kesiapan.py

echo.
echo ------------------------------------------------------------
echo  RIWAYAT PEMBLOKIRAN OLEH ZREV (kalau ada)
echo ------------------------------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$h = Get-Content 'C:\Quant\_MONITOR\jurnal.md' | Select-String 'BLK' | Select-Object -Last 10; if($h){ $h | ForEach-Object { Write-Host ('  ' + $_.Line) } } else { Write-Host '  (belum pernah ada pemblokiran tercatat)' }"

echo.
echo ------------------------------------------------------------
echo  TRADE ETERNA YANG SUDAH TERJADI (magic 920627 + 920641)
echo ------------------------------------------------------------
python -c "import MetaTrader5 as m,datetime as dt;m.initialize();d=[x for x in (m.history_deals_get(dt.datetime.now()-dt.timedelta(days=30),dt.datetime.now()+dt.timedelta(hours=6)) or []) if x.magic in (920627,920641)];print('  (belum ada trade eterna)') if not d else [print('    %s  magic %d  %-4s %-3s @ %.2f  %+.2f' % (dt.datetime.utcfromtimestamp(x.time).strftime('%%Y-%%m-%%d %%H:%%M'),x.magic,'BUY' if x.type==0 else 'SELL','IN' if x.entry==0 else 'OUT',x.price,x.profit)) for x in d];m.shutdown()"

echo.
pause
endlocal
