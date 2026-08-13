@echo off
cd /d D:\ai-video-pipeline
set PYTHONIOENCODING=utf-8
"C:\nvm4w\nodejs\node_modules\opencode-ai\bin\opencode.exe" run --auto --agent "Backend Architect" -- C:\Users\shrine\oc_task_video_p5_v2.txt > D:\ai-video-pipeline\p5_v2_run.log 2>&1
echo EXIT_CODE=%ERRORLEVEL% >> D:\ai-video-pipeline\p5_v2_run.log
