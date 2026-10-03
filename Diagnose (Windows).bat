@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Bitte zuerst einmal "Spielplan starten (Windows).bat" ausfuehren.
  pause
  exit /b 1
)
if exist diagnose rmdir /s /q diagnose
mkdir diagnose
echo Sammle Diagnose-Daten, bitte warten ...
".venv\Scripts\python.exe" spielplan.py --debug --nicht-oeffnen > diagnose\protokoll.txt 2>&1
powershell -NoProfile -Command "Compress-Archive -Path 'diagnose\*' -DestinationPath 'diagnose.zip' -Force"
echo.
echo Fertig. Die Datei diagnose.zip liegt jetzt im Ordner und ist markiert.
explorer /select,"%~dp0diagnose.zip"
pause
