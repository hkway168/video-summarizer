@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 视频转写助手 - 图形界面

rem 优先用 .venv，其次便携版 Python，最后系统 Python
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY if exist "tools\python-embed\python.exe" set "PY=tools\python-embed\python.exe"
if not defined PY (
    where pythonw >nul 2>nul && set "PY=pythonw"
)
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [错误] 没有找到 Python。
    echo        请直接双击 VideoSummarizer.exe（不需要 Python），
    echo        或先安装 Python 3.9+ 后重试。
    pause
    exit /b 1
)

echo 正在启动界面（%PY%）...
start "" %PY% app_gui.py
exit /b 0
