@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=
where py >nul 2>nul && set PY=py
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY (
  echo Python wurde nicht gefunden.
  echo Bitte installieren: https://www.python.org/downloads/
  echo Beim Installieren das Haekchen "Add python.exe to PATH" setzen.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Erster Start: richte alles ein, das dauert einen Moment ...
  %PY% -m venv .venv
)
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
".venv\Scripts\python.exe" spielplan.py
pause
