@echo off
cd /d D:\ai-video-pipeline
set PYTHONIOENCODING=utf-8
C:\nvm4w\nodejs\opencode.cmd run "execute task in C:\Users\shrine\oc_task_video_p4_rerun.txt" -f C:\Users\shrine\oc_task_video_p4_rerun.txt > D:\ai-video-pipeline\p4_rerun4.log 2>&1
echo EXIT_CODE=%ERRORLEVEL% >> D:\ai-video-pipeline\p4_rerun4.log
