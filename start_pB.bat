@echo off
cd /d D:\ai-video-pipeline
set PYTHONIOENCODING=utf-8
"C:\nvm4w\nodejs\node_modules\opencode-ai\bin\opencode.exe" run --auto --agent "Backend Architect" -- C:\Users\shrine\oc_task_b_overlay.txt > D:\ai-video-pipeline\pB_run.log 2>&1
echo EXIT_CODE=%ERRORLEVEL% >> D:\ai-video-pipeline\pB_run.log
