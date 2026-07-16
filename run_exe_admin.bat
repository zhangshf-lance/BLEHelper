@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~dp0dist\EmbeddedDebugAssistant.exe' -WorkingDirectory '%~dp0dist' -Verb RunAs"
