@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~dp0dist\BLEAssistant_v2.exe' -WorkingDirectory '%~dp0dist' -Verb RunAs"
