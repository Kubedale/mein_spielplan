# Spielplan als App einrichten (GitHub)

Einmalig, ca. 15 Minuten. Danach aktualisiert sich alles von selbst.

## 1. GitHub-Konto anlegen
https://github.com/signup  (kostenlos)

## 2. GitHub mit Claude verbinden
https://claude.ai/connect-github  und dort dem Zugriff zustimmen.

## 3. Leeres Projekt anlegen
1. https://github.com/new öffnen
2. **Repository name:** `spielplan`
3. **Public** auswählen (nötig für die kostenlose Webseite)
4. Keine Häkchen bei README, .gitignore oder Lizenz setzen
5. **Create repository**
6. Claude die Adresse schicken, z. B. `https://github.com/DEIN-NAME/spielplan`
   → Claude lädt alle Dateien hoch.

## 4. Webseite einschalten (nachdem Claude hochgeladen hat)
1. Im Projekt: **Settings** → links **Pages**
2. Unter „Build and deployment“ bei **Source**: **GitHub Actions** auswählen
3. Oben auf **Actions** → links **Spielplan aktualisieren** → rechts **Run workflow** → grüner Knopf **Run workflow**
4. Nach ca. 2–3 Minuten ist die Seite da: `https://DEIN-NAME.github.io/spielplan/`

## 5. Aufs Handy
1. Die Adresse aus Schritt 4 in **Chrome** auf dem Handy öffnen
2. Menü **⋮** → **App installieren** (oder „Zum Startbildschirm hinzufügen“)

## Neue Spielstätte hinzufügen
In der App oben rechts **+ Spielstätte** antippen (beim ersten Mal bei GitHub anmelden),
Formular ausfüllen, **Create** / **Submit new issue**. Nach 1–2 Minuten steht im Formular eine
Rückmeldung, ob es geklappt hat und wie viele Termine gefunden wurden.
Nur Formulare von dir selbst werden verarbeitet.

## Gut zu wissen
- Aktualisierung: täglich gegen 6 Uhr (Sommerzeit) bzw. 5 Uhr (Winterzeit). Sofort: Actions → Run workflow.
- GitHub schaltet die tägliche Aktualisierung ab, wenn 60 Tage nichts am Projekt geändert wurde.
  Die App zeigt dann einen Warnhinweis. Wieder einschalten: Actions → Spielplan aktualisieren → „Enable workflow“.
- Das Projekt ist öffentlich: Jeder mit dem Link kann die Spielstätten-Liste und den Spielplan sehen.
