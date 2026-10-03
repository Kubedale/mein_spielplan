# Spielplan-Sammler

## Einmalig installieren
```
pip install -r requirements.txt
```
(Python 3.9 oder neuer)

## Starten
```
python3 spielplan.py            # nächste 60 Tage, öffnet spielplan.html im Browser
python3 spielplan.py --tage 14
python3 spielplan.py --debug    # zeigt, welche Methode bei welchem Ort funktioniert hat
```

## Weitere Orte hinzufügen
In `orte.json` einfach Einträge ergänzen:
```json
[
  { "name": "Ballhaus Wedding", "url": "https://www.ballhauswedding.de/" },
  { "name": "Schaubühne", "url": "https://www.schaubuehne.de/", "programm_url": "https://www.schaubuehne.de/de/spielplan/" },
  { "name": "Irgendwo mit Kalender", "url": "https://beispiel.de/", "ical": "https://beispiel.de/kalender.ics" }
]
```
- `programm_url` (optional): direkte Adresse der Spielplan-Seite, wenn die automatische Suche sie nicht findet.
- `ical` (optional): Kalender-Feed (.ics), falls der Ort einen anbietet. Das ist die zuverlässigste Quelle.

## Wenn ein Ort 0 Termine liefert
Manche Seiten laden ihr Programm erst per JavaScript nach. Dann hilft meist:
1. Auf der Seite nach „Kalender abonnieren“, „iCal“ oder „.ics“ suchen und den Link als `ical` eintragen.
2. Die genaue Spielplan-Seite als `programm_url` eintragen.
3. Mit `--debug` laufen lassen und mir die Ausgabe schicken.

## Browser wählen
In `einstellungen.json` steht, in welchem Browser sich die Übersicht öffnet:
```json
{
  "browser": "chrome"
}
```
Möglich sind `"chrome"`, `"firefox"`, `"edge"` oder ein kompletter Pfad zu einem Browser.
Ohne diese Datei (oder wenn der Browser nicht gefunden wird) öffnet sich der Windows-Standardbrowser.
