@echo off
chcp 65001 >nul
REM ===============================================
REM 多平台视频转写工具 - 一键启动
REM 支持：YouTube / B 站 / 抖音 等
REM 用法：双击运行后粘贴视频 URL
REM ===============================================

cd /d "%~dp0"

echo.
echo ╔══════════════════════════════════════════════╗
echo ║  🎬 多平台视频转写工具                       ║
echo ║  (字幕优先 + GPU Whisper 兜底)               ║
echo ║  YouTube / B站 / 抖音 → Markdown 文档        ║
echo ╚══════════════════════════════════════════════╝
echo.

set /p URL="请粘贴视频 URL (YouTube / B站 / 抖音): "
if "%URL%"=="" (
    echo ❌ URL 不能为空
    pause
    exit /b 1
)

python main.py "%URL%" --output-dir ./output --download-dir ./downloads

echo.
echo 💡 提示：转写已完成，您可以将生成的 .transcript.md 文件拖给 AI 助手进行总结。
echo.
pause
