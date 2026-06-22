@echo off
title ORB Heartbeat Monitor
cd /d "C:\Users\msigf\OneDrive\Documents\Quant"
python -m pipeline.live.heartbeat
pause >nul
