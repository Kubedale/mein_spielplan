#!/usr/bin/env python3
"""Wird von GitHub ausgeführt, wenn das Formular "Neue Spielstätte" abgeschickt wurde.

Liest die Formularfelder aus der Umgebungsvariable ISSUE_BODY, testet die Spielstätte,
trägt sie bei Erfolg in orte.json ein und schreibt eine Rückmeldung nach ergebnis.md.
"""

import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

HIER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HIER))
import spielplan  # noqa: E402

FELDER = {"Name": "name", "Website": "url", "Spielplan-Seite (optional)": "programm_url",
          "Nur Spielort (optional)": "nur_spielort"}


def formular_lesen(text):
    """GitHub legt Formulare so ab: '### Feldname' + Leerzeile + Wert ('_No response_' = leer)."""
    werte = {}
    for block in re.split(r"^### ", text or "", flags=re.M)[1:]:
        kopf, _, wert = block.partition("\n")
        wert = wert.strip()
        if kopf.strip() in FELDER and wert and wert != "_No response_":
            werte[FELDER[kopf.strip()]] = wert
    return werte


def ist_webadresse(text):
    teile = urlparse(text)
    return teile.scheme in ("http", "https") and "." in teile.netloc


def main():
    ausgabe = HIER / "ergebnis.md"
    ok = False
    try:
        ort = formular_lesen(os.environ.get("ISSUE_BODY", ""))
        orte = json.loads((HIER / "orte.json").read_text(encoding="utf-8"))
        fehler = []
        if not ort.get("name"):
            fehler.append("Der **Name** fehlt.")
        for feld in ("url", "programm_url"):
            if ort.get(feld) and not ist_webadresse(ort[feld]):
                fehler.append(f"`{ort[feld]}` ist keine gültige Webadresse (sie muss mit https:// beginnen).")
        if not ort.get("url"):
            fehler.append("Die **Website** fehlt.")
        if any(o["name"].lower() == ort.get("name", "").lower() for o in orte):
            fehler.append(f"Eine Spielstätte namens **{ort['name']}** gibt es schon.")
        if fehler:
            text = "❌ Nicht eingetragen:\n\n" + "\n".join(f"- {f}" for f in fehler)
        else:
            heute = date.today()
            termine, methode = spielplan.sammle_ort(ort, heute + timedelta(days=120))
            termine = [t for t in termine if heute <= t.datum <= heute + timedelta(days=120)]
            if ort.get("nur_spielort"):
                f = ort["nur_spielort"].lower()
                termine = [t for t in termine if f in (t.spielort or t.titel).lower()]
            if termine:
                orte.append(ort)
                (HIER / "orte.json").write_text(json.dumps(orte, ensure_ascii=False, indent=2) + "\n",
                                                encoding="utf-8")
                ok = True
                beispiele = "\n".join(
                    f"- {t.datum:%d.%m.} {t.uhrzeit or ''} {t.titel}" for t in sorted(termine, key=lambda t: (t.datum, t.uhrzeit))[:8])
                text = (f"✅ **{ort['name']}** ist eingetragen: {len(termine)} Termine in den nächsten 120 Tagen "
                        f"(Methode: {methode}).\n\nDie ersten Termine:\n{beispiele}\n\n"
                        "Bitte kurz prüfen, ob Titel und Uhrzeiten stimmen. Der Spielplan wird gerade neu erstellt "
                        "und ist in ein paar Minuten in der App.")
            else:
                text = (f"⚠️ Bei **{ort['name']}** habe ich keine Termine gefunden, deshalb ist die Spielstätte "
                        "noch nicht eingetragen.\n\nMögliche Gründe: Die Seite lädt ihr Programm erst nachträglich, "
                        "oder ihr Aufbau ist dem Programm noch unbekannt. Hilfreich: die genaue Spielplan-Seite "
                        "angeben (neues Formular) oder das Programm für diese Seite erweitern lassen.")
    except Exception as e:  # Rückmeldung statt stillem Abbruch
        text = f"❌ Unerwarteter Fehler: `{type(e).__name__}: {e}`"
    ausgabe.write_text(text + "\n", encoding="utf-8")
    print(text)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"ok={'true' if ok else 'false'}\n")


if __name__ == "__main__":
    main()
