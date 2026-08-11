@echo off
rem Jendela pantau. AMAN DITUTUP - ini cuma penonton, mesinnya di Task Scheduler.
title HEARTBEAT TRADING
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_MONITOR\heartbeat_live.ps1"
