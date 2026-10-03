#!/bin/bash
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python wurde nicht gefunden. Bitte installieren: https://www.python.org/downloads/"
  read -p "Enter zum Schliessen"; exit 1
fi
if [ ! -x .venv/bin/python ]; then
  echo "Erster Start: richte alles ein, das dauert einen Moment ..."
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
.venv/bin/python spielplan.py
read -p "Fertig. Enter zum Schliessen"
