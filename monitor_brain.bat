@echo off
title ORB Heartbeat Monitor
cd /d "%~dp0"
python -m pipeline.live.heartbeat
pause >nul
