@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
title Fluffy Vanilla Bot
echo Запуск бота...
python main.py
pause
