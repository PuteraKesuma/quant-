@echo off
setlocal
title SHADOW ADVISOR - insight only, tidak nge-trade (boleh ditutup kapan saja)
cd /d "%~dp0"

echo.
echo ============================================================
echo            SHADOW ADVISOR (LLM insight, READ-ONLY)
echo ============================================================
echo  Proses ini TIDAK nge-trade. Dia cuma:
echo    - pantau MT5 untuk posisi BARU dari brain (via magic)
echo    - capture chart + tanya Claude konfirmasi makro/mikro
echo    - catat verdict (CONFIRM/CAUTION) ke advisor_journal.jsonl
echo  Aman ditutup kapan saja - trading TIDAK terpengaruh.
echo  Butuh: MT5 terbuka+login, dan ANTHROPIC_API_KEY di .env
echo ============================================================
echo.
python -m pipeline.live.advisor

echo.
echo ------------------------------------------------------------
echo  Advisor berhenti. Tekan tombol apa saja untuk menutup.
pause >nul
endlocal
