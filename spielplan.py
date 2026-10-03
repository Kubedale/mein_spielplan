#!/usr/bin/env python3
"""
Spielplan-Sammler: holt die Programme deiner Lieblingsorte und baut daraus
eine gemeinsame Übersicht (spielplan.html), sortiert nach Datum.

Die Orte stehen in orte.json. Für jeden Ort probiert das Skript nacheinander:
  1. einen Kalender-Feed (iCal/.ics), falls angegeben oder auf der Seite verlinkt
  2. die Schnittstelle des WordPress-Plugins "The Events Calendar"
  3. strukturierte Daten auf der Seite (schema.org Event / JSON-LD)
  4. als Notlösung: Links auf der Seite, in deren Nähe ein Datum steht

Aufruf:
  python3 spielplan.py              # nächste 120 Tage
  python3 spielplan.py --tage 30
  python3 spielplan.py --debug      # zeigt, welche Methode bei welchem Ort griff
"""

import argparse
import html
import json
import os
import shutil
import subprocess
import re
import sys
import time
import webbrowser
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup



class _BerlinZeit(tzinfo):
    """Ersatz, falls Windows keine Zeitzonen-Daten hat: MEZ/MESZ nach EU-Regel
    (Sommerzeit vom letzten Sonntag im März bis zum letzten Sonntag im Oktober, je 01:00 UTC)."""

    @staticmethod
    def _letzter_sonntag(jahr, monat):
        d = date(jahr, monat, 31)
        return d - timedelta(days=(d.weekday() + 1) % 7)

    def _sommer_utc(self, utc):
        beginn = datetime.combine(self._letzter_sonntag(utc.year, 3), datetime.min.time()) + timedelta(hours=1)
        ende = datetime.combine(self._letzter_sonntag(utc.year, 10), datetime.min.time()) + timedelta(hours=1)
        return beginn <= utc < ende

    def utcoffset(self, dt):
        lokal = dt.replace(tzinfo=None)
        return timedelta(hours=2) if self._sommer_utc(lokal - timedelta(hours=1)) else timedelta(hours=1)

    def dst(self, dt):
        return self.utcoffset(dt) - timedelta(hours=1)

    def fromutc(self, dt):
        utc = dt.replace(tzinfo=None)
        return (utc + timedelta(hours=2 if self._sommer_utc(utc) else 1)).replace(tzinfo=self)

    def tzname(self, dt):
        return "MESZ" if self.dst(dt) else "MEZ"


try:
    BERLIN = ZoneInfo("Europe/Berlin")
except Exception:  # z. B. Windows ohne Paket "tzdata"
    BERLIN = _BerlinZeit()
HIER = Path(__file__).resolve().parent
HEADERS = {
    # ein üblicher Browser-Kennzeichner, sonst zeigen manche Seiten nur einen Browser-Hinweis
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept-Language": "de-DE,de;q=0.9",
}
TIMEOUT = 20
DEBUG = False

PROGRAMM_WOERTER = ("programm", "spielplan", "kalender", "termine", "events",
                    "veranstaltungen", "calendar", "schedule")
MONATE = {
    "januar": 1, "jan": 1, "februar": 2, "feb": 2, "märz": 3, "maerz": 3, "mär": 3,
    "mrz": 3, "april": 4, "apr": 4, "mai": 5, "juni": 6, "jun": 6, "juli": 7,
    "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "oktober": 10, "okt": 10, "november": 11, "nov": 11, "dezember": 12, "dez": 12,
}


@dataclass
class Termin:
    ort: str
    titel: str
    datum: date
    uhrzeit: str  # "19:30" oder "" wenn unbekannt
    link: str
    spielort: str = ""  # z. B. "Theater Stralsund", falls ein Haus mehrere Bühnen hat

    @property
    def schluessel(self):
        return (self.ort, self.datum, self.uhrzeit, self.titel.lower().strip())


def log(*a):
    if DEBUG:
        print("   ·", *a, file=sys.stderr)


def _kodierung_pruefen(r):
    """Manche ältere Seiten geben UTF-8 an, liefern aber Windows-Zeichen (oder umgekehrt)."""
    if "charset" in r.headers.get("content-type", "").lower() and r.encoding:
        try:
            r.content.decode(r.encoding)
            return
        except (UnicodeDecodeError, LookupError):
            pass
    # überwiegend gültiges UTF-8 (einzelne kaputte Zeichen sind egal) -> UTF-8, sonst Windows-1252
    fehler = r.content.decode("utf-8", errors="replace").count("\ufffd")
    r.encoding = "utf-8" if fehler < 30 else "cp1252"


def hole(url, **kw):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, **kw)
    _kodierung_pruefen(r)
    log(f"HTTP {r.status_code} {url} ({len(r.content)} Bytes)")
    if DEBUG:  # Seite für die Fehlersuche im Ordner "diagnose" ablegen
        ordner = HIER / "diagnose"
        ordner.mkdir(exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9]+", "_", url.split("://", 1)[-1]).strip("_")[:120]
        (ordner / f"{name}.html").write_text(r.text, encoding="utf-8", errors="replace")
    r.raise_for_status()
    return r


def sauber(text):
    text = html.unescape(text or "").replace("\xad", "")  # weiche Trennstriche entfernen
    return re.sub(r"\s+", " ", text).strip()


def parse_zeitpunkt(wert):
    """ISO-Datum/-Zeit (auch mit Zeitzone) -> (date, 'HH:MM' oder '')."""
    if not wert:
        return None
    wert = wert.strip()
    try:
        if len(wert) == 10:
            return date.fromisoformat(wert), ""
        dt = datetime.fromisoformat(wert.replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.astimezone(BERLIN)
        return dt.date(), dt.strftime("%H:%M")
    except ValueError:
        return None


# ---------------------------------------------------------------- 1. iCal

def finde_ical_links(soup, basis):
    links = []
    for tag in soup.find_all(["a", "link"], href=True):
        href = tag["href"]
        if (href.startswith("webcal:") or ".ics" in href.lower()
                or "ical=1" in href.lower() or tag.get("type") == "text/calendar"):
            links.append(urljoin(basis, href.replace("webcal://", "https://")))
    return list(dict.fromkeys(links))


def lies_ical(text, ort, standard_link):
    text = re.sub(r"\r?\n[ \t]", "", text)  # gefaltete Zeilen zusammenfügen
    termine = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        felder = {}
        for zeile in block.strip().splitlines():
            if ":" not in zeile:
                continue
            kopf, wert = zeile.split(":", 1)
            name, *param = kopf.split(";")
            felder[name.upper()] = (param, wert)
        if "DTSTART" not in felder:
            continue
        param, wert = felder["DTSTART"]
        try:
            if len(wert) == 8:
                d, uhr = datetime.strptime(wert, "%Y%m%d").date(), ""
            else:
                dt = datetime.strptime(wert.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
                if wert.endswith("Z"):
                    dt = dt.replace(tzinfo=timezone.utc).astimezone(BERLIN)
                else:
                    tzid = next((p[5:] for p in param if p.upper().startswith("TZID=")), None)
                    if tzid:
                        try:
                            dt = dt.replace(tzinfo=BERLIN if "berlin" in tzid.lower() else ZoneInfo(tzid)).astimezone(BERLIN)
                        except Exception:
                            pass
                d, uhr = dt.date(), dt.strftime("%H:%M")
        except ValueError:
            continue
        titel = felder.get("SUMMARY", ([], ""))[1].replace("\\,", ",").replace("\\;", ";")
        link = felder.get("URL", ([], standard_link))[1]
        termine.append(Termin(ort, sauber(titel), d, uhr, link))
    return termine


# ------------------------------------------------- 2. The Events Calendar

def lies_tribe_api(basis, ort):
    origin = "{0.scheme}://{0.netloc}".format(urlparse(basis))
    url = f"{origin}/wp-json/tribe/events/v1/events?per_page=50&start_date={date.today()}"
    termine = []
    for _ in range(10):  # höchstens 10 Seiten
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
            break
        daten = r.json()
        for ev in daten.get("events", []):
            zp = parse_zeitpunkt((ev.get("start_date") or "").replace(" ", "T"))
            if zp:
                uhr = "" if ev.get("all_day") else zp[1]
                termine.append(Termin(ort, sauber(BeautifulSoup(ev.get("title", ""), "html.parser").get_text()),
                                      zp[0], uhr, ev.get("url") or basis))
        url = daten.get("next_rest_url")
        if not url:
            break
    return termine


# ------------------------------------------------------- 3. JSON-LD Event

def lies_jsonld(soup, ort, basis):
    termine = []

    def durchlaufe(obj):
        if isinstance(obj, list):
            for o in obj:
                durchlaufe(o)
        elif isinstance(obj, dict):
            typ = obj.get("@type", "")
            typen = typ if isinstance(typ, list) else [typ]
            if any(str(t).endswith("Event") for t in typen) and obj.get("startDate"):
                zp = parse_zeitpunkt(str(obj["startDate"]))
                if zp:
                    termine.append(Termin(ort, sauber(str(obj.get("name", ""))), zp[0], zp[1],
                                          urljoin(basis, str(obj.get("url") or basis))))
            for wert in obj.values():
                if isinstance(wert, (list, dict)):
                    durchlaufe(wert)

    for skript in soup.find_all("script", type="application/ld+json"):
        try:
            durchlaufe(json.loads(skript.string or ""))
        except (json.JSONDecodeError, TypeError):
            continue
    return termine


# --------------------------------------------- 4. Notlösung: HTML-Heuristik

# Jahr: vierstellig (auch nach Leerzeichen) oder zweistellig direkt dahinter;
# so wird bei "08.10. 20:00" die Uhrzeit nicht als Jahr gelesen
DATUM_NUM = re.compile(r"\b(\d{1,2})\.\s?(\d{1,2})\.(?:\s?(\d{4})|(\d{2}))?(?![\d:])")
DATUM_TEXT = re.compile(r"\b(\d{1,2})\.?\s+(" + "|".join(sorted(MONATE, key=len, reverse=True))
                        + r")\.?(\s+(\d{4}))?\b", re.I)
UHRZEIT = re.compile(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\s*(?:uhr|h)?\b", re.I)


def jahr_ergaenzen(tag, monat, jahr_text):
    heute = date.today()
    if jahr_text:
        jahr = int(jahr_text)
        jahr += 2000 if jahr < 100 else 0
        return date(jahr, monat, tag)
    kandidat = date(heute.year, monat, tag)
    # Datum ohne Jahr, das deutlich in der Vergangenheit liegt -> nächstes Jahr
    if kandidat < heute - timedelta(days=60):
        kandidat = date(heute.year + 1, monat, tag)
    return kandidat


def finde_datum(text):
    for m in DATUM_NUM.finditer(text):
        try:
            return jahr_ergaenzen(int(m.group(1)), int(m.group(2)), m.group(3) or m.group(4))
        except ValueError:
            continue
    for m in DATUM_TEXT.finditer(text):
        try:
            return jahr_ergaenzen(int(m.group(1)), MONATE[m.group(2).lower()], m.group(4))
        except ValueError:
            continue
    return None


def lies_html_heuristik(soup, ort, basis):
    termine = []
    eigene_domain = urlparse(basis).netloc
    for a in soup.find_all("a", href=True):
        link = urljoin(basis, a["href"])
        if urlparse(link).netloc != eigene_domain or link.rstrip("/") == basis.rstrip("/"):
            continue
        # den kleinsten umgebenden Block suchen, in dem ein Datum steht
        knoten = a
        for _ in range(4):
            text = sauber(knoten.get_text(" "))
            zeit_tag = knoten.find("time", datetime=True) if hasattr(knoten, "find") else None
            datum, uhr = None, ""
            if zeit_tag:
                zp = parse_zeitpunkt(zeit_tag["datetime"])
                if zp:
                    datum, uhr = zp
            if not datum and len(text) < 500:
                datum = finde_datum(text)
            if datum:
                if not uhr:
                    m = UHRZEIT.search(DATUM_NUM.sub(" ", text))
                    uhr = f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""
                ueberschrift = knoten.find(["h1", "h2", "h3", "h4", "h5"]) if knoten is not a else None
                titel = sauber((ueberschrift or a).get_text(" "))
                if len(titel) >= 3 and not DATUM_NUM.fullmatch(titel):
                    termine.append(Termin(ort, titel[:160], datum, uhr, link))
                break
            if knoten.parent is None:
                break
            knoten = knoten.parent
    return termine


# ------------------------- 3b. Termin-Daten, die als JSON im Seitencode stecken

def lies_eingebettete_daten(soup, ort, basis):
    """z. B. Junges Tanzhaus: {"title": ..., "url": ..., "event_datetimes": [{"datetime": ...}]}
    in Attributen von Web-Komponenten oder in <script>-Blöcken."""
    termine = []

    def durchlaufe(obj):
        if isinstance(obj, list):
            for o in obj:
                durchlaufe(o)
        elif isinstance(obj, dict):
            zeiten = obj.get("event_datetimes")
            if isinstance(obj.get("title"), str) and isinstance(zeiten, list):
                link = urljoin(basis, str(obj.get("full_url") or obj.get("url") or basis))
                for z in zeiten:
                    zp = parse_zeitpunkt(str(z.get("datetime", ""))) if isinstance(z, dict) else None
                    if zp:
                        termine.append(Termin(ort, sauber(obj["title"]), zp[0], zp[1], link))
            for wert in obj.values():
                if isinstance(wert, (list, dict)):
                    durchlaufe(wert)

    texte = [w for tag in soup.find_all(True) for w in tag.attrs.values() if isinstance(w, str)]
    texte += [sk.string or "" for sk in soup.find_all("script")]
    for text in texte:
        if "event_datetimes" not in text or text.lstrip()[:1] not in ("{", "["):
            continue
        try:
            if "\\u0022" in text:  # Junges Tanzhaus: Anführungszeichen als \u0022 kodiert
                text = json.loads('"' + text.replace('"', '\\"') + '"')
            durchlaufe(json.loads(text))
        except json.JSONDecodeError:
            continue
    return termine


# ---------------------- 3c. Programm als Textzeilen ("Do 8.10. 19:30 Uhr")

WOCHENTAG_NR = {"mo": 0, "di": 1, "mi": 2, "do": 3, "fr": 4, "sa": 5, "so": 6}
ZEILE_DATUM = re.compile(
    r"^(?:(mo|di|mi|do|fr|sa|so)[a-z]*\.?,?\s+)?"
    r"(\d{1,2})\.\s?(?:(\d{1,2})\.|(" + "|".join(sorted(MONATE, key=len, reverse=True)) + r")\.?)"
    r"(?:\s?(\d{4}))?(?![\d:])", re.I)
ZEILE_ZEIT = re.compile(r"^[\s,|·–-]*(?:um\s*|ab\s*)?(2[0-3]|[01]?\d)(?!\d)(?:[:.]([0-5]\d))?"
                        r"(?:\s*[-–]\s*\d{1,2}(?:[:.][0-5]\d)?)?(?:\s*(uhr|h)\b)?", re.I)
NUR_ZEIT = re.compile(r"^(?:um\s*|ab\s*)?([01]?\d|2[0-3])(?:([:.][0-5]\d))?"
                      r"(?:\s*[-–]\s*\d{1,2}(?:[:.][0-5]\d)?)?\s*(uhr|h)?$", re.I)
KEIN_TITEL = re.compile(r"^(tickets?|mehr infos?|mehr|info|-|–|\d.*€.*|.*€\s*(ak|vvk)?)$", re.I)


def lies_textzeilen(soup, ort, basis):
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    zeilen = [z.replace("​", "").strip() for z in soup.get_text("\n").splitlines()]
    zeilen = _zeilen_zusammenfuehren([z for z in zeilen if z])
    termine = []
    letzte_zeile = {}
    for i, zeile in enumerate(zeilen):
        m = ZEILE_DATUM.match(zeile)
        if not m:
            continue
        monat = int(m.group(3)) if m.group(3) else MONATE[m.group(4).lower()]
        try:
            datum = jahr_ergaenzen(int(m.group(2)), monat, m.group(5))
        except ValueError:
            continue
        if m.group(1):  # Wochentag angegeben: muss zum Datum passen
            soll = WOCHENTAG_NR[m.group(1).lower()]
            if datum.weekday() != soll:
                passend = [d for d in (date(datum.year - 1, monat, 1), date(datum.year + 1, monat, 1))
                           if not m.group(5)]
                datum = next((d.replace(day=int(m.group(2))) for d in passend
                              if _sicher(d, int(m.group(2))) and d.replace(day=int(m.group(2))).weekday() == soll), None)
                if not datum:
                    continue
        rest = zeile[m.end():]
        uhr = ""
        z = ZEILE_ZEIT.match(rest)
        if z and (z.group(2) or z.group(3)):  # "19:30", "20 Uhr", "13 - 15 Uhr"
            uhr = f"{int(z.group(1)):02d}:{z.group(2) or '00'}"
            rest = rest[z.end():]
        titel = rest.strip(" ,|·–-:")
        # Wiederholung des Datums mit Uhrzeit-Detail (z. B. Wix: "Sa., 24. Okt." … "24. Okt. 2026, 19:00 – 20:30")
        # oder dasselbe Datum noch einmal im Beschreibungstext (Pianosalon: "15.12. klappt.")
        if (termine and termine[-1].datum == datum and i - letzte_zeile.get("i", -99) <= 6
                and (not titel or not uhr)):
            if uhr and not termine[-1].uhrzeit:
                termine[-1].uhrzeit = uhr
            continue
        j = i + 1
        if not uhr and j < len(zeilen):  # Uhrzeit steht in der nächsten Zeile
            z = NUR_ZEIT.match(zeilen[j])
            if z and (z.group(2) or z.group(3)):  # "19:30" oder "20 Uhr", keine nackte Zahl
                uhr = f"{int(z.group(1)):02d}:{(z.group(2) or ':00')[1:]}"
                j += 1
        if len(titel) < 3:
            titel = ""
            start = j
            while j < len(zeilen) and j < start + 3 and not titel:
                if ZEILE_DATUM.match(zeilen[j]):
                    break
                if not KEIN_TITEL.match(zeilen[j]):
                    titel = zeilen[j]
                j += 1
        # Fortsetzungszeilen wie "- die Ballhaus Show" anhängen
        while titel and j < len(zeilen) and len(titel) < 120 and (
                zeilen[j].startswith(("-", "–")) or titel.endswith(("-", "–"))):
            if ZEILE_DATUM.match(zeilen[j]):
                break
            titel = (titel.rstrip(" -–") + " – " + zeilen[j].lstrip(" -–")).strip(" -–")
            j += 1
        if not titel:
            continue
        if "abgesagt" in zeile.lower() or (i > 0 and "abgesagt" in zeilen[i - 1].lower()):
            titel = "[abgesagt] " + titel
        termine.append(Termin(ort, sauber(titel)[:160], datum, uhr, basis))
        letzte_zeile["i"] = i
    return termine


WOCHENTAG_ALLEIN = re.compile(r"^(mo|di|mi|do|fr|sa|so)[a-z]*\.?,?$", re.I)
TAG_ALLEIN = re.compile(r"^\d{1,2}\.?$")
MONAT_ZEILE = re.compile(r"^(" + "|".join(sorted(MONATE, key=len, reverse=True)) + r")\.?(\s+\d{4})?$", re.I)


def _zeilen_zusammenfuehren(zeilen):
    """'So' / '04' / 'Okt 2026'  ->  'So 04. Okt 2026' (z. B. Pianosalon Christophori)."""
    ergebnis, i = [], 0
    while i < len(zeilen):
        z = zeilen[i]
        if (WOCHENTAG_ALLEIN.match(z) and i + 2 < len(zeilen) and TAG_ALLEIN.match(zeilen[i + 1])
                and MONAT_ZEILE.match(zeilen[i + 2])):
            ergebnis.append(f"{z.rstrip('.,')} {zeilen[i + 1].rstrip('.')}. {zeilen[i + 2]}")
            i += 3
        elif TAG_ALLEIN.match(z) and i + 1 < len(zeilen) and MONAT_ZEILE.match(zeilen[i + 1]):
            ergebnis.append(f"{z.rstrip('.')}. {zeilen[i + 1]}")
            i += 2
        else:
            ergebnis.append(z)
            i += 1
    return ergebnis


def _sicher(d, tag):
    try:
        d.replace(day=tag)
        return True
    except ValueError:
        return False


# ------------------- 3d. Wix-Seiten: Termin-Seiten aus der Sitemap lesen

def lies_wix_sitemap(basis, ort):
    """Wix-Veranstaltungen werden per JavaScript nachgeladen. Ihre Einzelseiten
    (…/event-details/…) stehen aber in der Sitemap und enthalten oft Termin-Daten."""
    origin = "{0.scheme}://{0.netloc}".format(urlparse(basis))
    xml = hole(origin + "/sitemap.xml").text
    if "event" not in xml.lower():
        return []

    def eintraege(text):
        return [(u.group(1), (re.search(r"<lastmod>([^<]+)", u.group(0)) or [None, ""])[1])
                for u in re.finditer(r"<(?:url|sitemap)>\s*<loc>([^<]+)</loc>.*?</(?:url|sitemap)>", text, re.S)]

    seiten = []
    for loc, geaendert in eintraege(xml):
        if "event" not in loc.lower():
            continue
        if loc.endswith(".xml"):
            seiten += eintraege(hole(loc).text)
        else:
            seiten.append((loc, geaendert))
    seiten = [s for s in seiten if "event-details" in s[0] or "/events/" in s[0]]
    seiten.sort(key=lambda s: s[1], reverse=True)  # neueste zuerst
    log(f"Sitemap: {len(seiten)} Veranstaltungsseiten, lese die neuesten 40")
    termine = []
    for loc, _ in seiten[:40]:
        time.sleep(1.5)  # höflich bleiben, Wix bremst sonst mit "zu viele Anfragen"
        try:
            soup = BeautifulSoup(hole(loc).text, "html.parser")
        except requests.RequestException:
            time.sleep(5)
            try:
                soup = BeautifulSoup(hole(loc).text, "html.parser")
            except requests.RequestException:
                continue
        termine += lies_jsonld(soup, ort, loc)
    return termine


# ------------- 3e. Spielplan-Liste mit Spielort je Termin (z. B. Theater Vorpommern)

def lies_spielplan_liste(soup, ort, basis):
    """<li class="schedule-list-item" data-date="10_2026"> mit Tag, Uhrzeit, Spielort und Titel."""
    termine = []
    for eintrag in soup.select("li.schedule-list-item"):
        monat_jahr = re.match(r"(\d{1,2})_(\d{4})", eintrag.get("data-date", ""))
        tag = eintrag.select_one(".day")
        titel = eintrag.select_one(".title")
        if not (monat_jahr and tag and titel and tag.get_text(strip=True).isdigit()):
            continue
        try:
            datum = date(int(monat_jahr.group(2)), int(monat_jahr.group(1)), int(tag.get_text(strip=True)))
        except ValueError:
            continue
        zeit_text = eintrag.select_one(".time").get_text(" ", strip=True) if eintrag.select_one(".time") else ""
        z = re.search(r"(\d{1,2}):(\d{2})", zeit_text)
        spielort = eintrag.select_one(".place")
        link = eintrag.select_one("a[href^='/'], a[href*='/programm/']")
        name = sauber(titel.get_text(" "))
        if "entfällt" in zeit_text.lower():
            name = "[entfällt] " + name
        termine.append(Termin(ort, name, datum, f"{int(z.group(1)):02d}:{z.group(2)}" if z else "",
                              urljoin(basis, link["href"]) if link else basis,
                              sauber(spielort.get_text(" ")) if spielort else ""))
    return termine


# ----------- 3f. Kalender-Widgets mit eigener Datenquelle (z. B. ticket.io / FullCalendar)

def lies_kalender_datenquelle(soup, ort, basis):
    """<div class="event-calendar" data-eventurl="/?view=calendar&getCal=1" data-firstdate=… data-lastdate=…>
    Der Kalender lädt seine Termine als JSON-Liste (title, start, url) von data-eventurl."""
    termine = []
    for el in soup.find_all(attrs={"data-eventurl": True}):
        von = el.get("data-firstdate") or date.today().isoformat()
        bis = el.get("data-lastdate") or (date.today() + timedelta(days=180)).isoformat()
        quelle = urljoin(basis, el["data-eventurl"])
        trenner = "&" if "?" in quelle else "?"
        r = hole(f"{quelle}{trenner}start={von}&end={bis}")
        try:
            daten = r.json()
        except ValueError:
            log("Kalender-Datenquelle liefert kein JSON")
            continue
        if isinstance(daten, dict):
            daten = daten.get("events") or daten.get("data") or []
        for ev in daten if isinstance(daten, list) else []:
            if not isinstance(ev, dict) or not ev.get("start"):
                continue
            zp = parse_zeitpunkt(str(ev["start"]).replace(" ", "T"))
            titel = sauber(BeautifulSoup(str(ev.get("title", "")), "html.parser").get_text(" "))
            # ticket.io setzt vor den Titel noch einmal das Datum: "03.10. Gutes Wedding …"
            titel = re.sub(r"^\d{1,2}\.\d{1,2}\.(\d{2,4})?\s*[-–:]?\s*", "", titel)
            if zp and titel:
                # "allDay" ist bei ticket.io immer gesetzt; eine echte Uhrzeit trotzdem übernehmen
                uhr = "" if ev.get("allDay") and zp[1] == "00:00" else zp[1]
                klassen = ev.get("className") or []
                klassen = " ".join(klassen) if isinstance(klassen, list) else str(klassen)
                if "no-tickets-left" in klassen or "sold" in klassen:
                    titel = "[ausverkauft] " + titel
                termine.append(Termin(ort, titel, zp[0], uhr, urljoin(quelle, str(ev.get("url") or basis))))
    return termine


# ------------------------------------------------------------ Steuerung

def programmseiten(soup, basis):
    """Links auf der Startseite, die nach Programm/Spielplan aussehen."""
    gefunden = []
    for a in soup.find_all("a", href=True):
        ziel = urljoin(basis, a["href"])
        if urlparse(ziel).netloc != urlparse(basis).netloc:
            continue
        kennung = (a.get_text(" ") + " " + a["href"]).lower()
        if any(w in kennung for w in PROGRAMM_WOERTER) and not any(
                w in kennung for w in ("vergangen", "archiv", "rueckblick", "rückblick")):
            gefunden.append(ziel.split("#")[0])
    return list(dict.fromkeys(gefunden))[:3]


def sammle_ort(ort, bis=None):
    name, basis = ort["name"], ort["url"]
    versuche = []

    if ort.get("ical"):
        versuche.append(("iCal (aus orte.json)", lambda: lies_ical(hole(ort["ical"]).text, name, basis)))

    seiten = {}

    def seite(url):
        if url not in seiten:
            seiten[url] = BeautifulSoup(hole(url).text, "html.parser")
        return seiten[url]

    gemerkt = []

    def kandidaten():
        if gemerkt:
            return gemerkt
        angabe = ort.get("programm_url")
        urls = [angabe] if isinstance(angabe, str) else list(angabe or [])
        if not urls:
            urls = [basis] + programmseiten(seite(basis), basis)
        # eingebettete Unterseiten (iframes) derselben Website mitlesen
        for url in urls:
            if len(urls) >= 8:
                break
            try:
                rahmen = seite(url).find_all(["iframe", "frame"], src=True)
            except requests.RequestException:
                continue
            for f in rahmen:
                ziel = urljoin(url, f["src"])
                if urlparse(ziel).netloc == urlparse(url).netloc and ziel not in urls:
                    urls.append(ziel)
        gemerkt.extend(dict.fromkeys(urls))
        return gemerkt

    def ical_entdeckt():
        for url in kandidaten():
            for feed in finde_ical_links(seite(url), url):
                log("iCal-Feed gefunden:", feed)
                t = lies_ical(hole(feed).text, name, url)
                if t:
                    return t
        return []

    def ueber_seiten(funktion):
        def lauf():
            alle = []
            for url in kandidaten():
                log("lese", url)
                try:
                    soup = seite(url)
                except requests.RequestException as e:
                    log(f"{url}: {e}")
                    continue
                alle += funktion(BeautifulSoup(str(soup), "html.parser"), name, url)
            return alle
        return lauf

    versuche += [
        ("iCal-Feed", ical_entdeckt),
        ("WordPress The Events Calendar", lambda: lies_tribe_api(basis, name)),
        ("Spielplan-Liste", ueber_seiten(lies_spielplan_liste)),
        ("Kalender-Datenquelle", ueber_seiten(lies_kalender_datenquelle)),
        ("strukturierte Daten (JSON-LD)", ueber_seiten(lies_jsonld)),
        ("eingebettete Termin-Daten", ueber_seiten(lies_eingebettete_daten)),
        ("Programm-Text", ueber_seiten(lies_textzeilen)),
        ("Wix-Veranstaltungsseiten", lambda: lies_wix_sitemap(basis, name)),
        ("HTML-Heuristik", ueber_seiten(lies_html_heuristik)),
    ]

    for methode, lauf in versuche:
        try:
            termine = lauf()
        except requests.RequestException as e:
            log(f"{methode}: Netzwerkfehler {e}")
            continue
        alle_gefunden = len(termine)
        # nur Termine im gewünschten Zeitraum zählen; ein einzelner Treffer weit in der
        # Zukunft (z. B. ein Datum im Fließtext) soll die besseren Methoden nicht verdrängen
        termine = [t for t in termine if date.today() <= t.datum <= (bis or date.max)]
        log(f"{methode}: {len(termine)} kommende Termine (insgesamt {alle_gefunden})")
        if termine:
            return termine, methode
    return [], None


# --------------------------------------------------------------- Ausgabe

# Zusätze für die Web-App (GitHub Pages): installierbar auf dem Handy, startet im Vollbild
APP_KOPF = """<link rel="manifest" href="manifest.webmanifest">
<meta name="theme-color" content="#8a2d3b">
<link rel="icon" href="icon-192.png">
<link rel="apple-touch-icon" href="icon-192.png">"""
APP_SKRIPT = """if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});"""

WOCHENTAGE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def schreibe_html(termine, pfad, berichte, app=False, formular_url=""):
    orte = sorted({t.ort for t in termine})
    tage = {}
    for t in termine:
        tage.setdefault(t.datum, []).append(t)

    def knopf(o):
        return f'<button class="chip" data-ort="{html.escape(o)}">{html.escape(o)}</button>'

    abschnitte = []
    for d in sorted(tage):
        zeilen = "".join(
            f'<li data-ort="{html.escape(t.ort)}"><span class="zeit">{t.uhrzeit or "–"}</span>'
            f'<a href="{html.escape(t.link)}" target="_blank" rel="noopener">{html.escape(t.titel)}</a>'
            f'<span class="ort">{html.escape(t.ort)}'
            f'{"<br>" + html.escape(t.spielort) if t.spielort and t.spielort != t.ort else ""}</span></li>'
            for t in sorted(tage[d], key=lambda t: (t.uhrzeit or "99", t.ort)))
        abschnitte.append(f'<section data-datum="{d.isoformat()}"><h2>{WOCHENTAGE[d.weekday()]}, {d:%d.%m.%Y}</h2><ul>{zeilen}</ul></section>')

    status = "".join(f"<li>{html.escape(n)}: {html.escape(b)}</li>" for n, b in berichte)
    seite = f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mein Spielplan</title>
{APP_KOPF if app else ""}
<style>
 :root {{ --bg:#faf8f5; --fg:#1d1b18; --muted:#6b655c; --line:#e4dfd7; --accent:#8a2d3b; }}
 @media (prefers-color-scheme: dark) {{ :root {{ --bg:#17161a; --fg:#ece8e1; --muted:#a39d93; --line:#2e2c31; --accent:#e08a97; }} }}
 body {{ background:var(--bg); color:var(--fg); font:16px/1.5 system-ui, sans-serif; margin:0 auto; max-width:820px; padding:24px 16px; }}
 h1 {{ font-size:1.6rem; margin:0 0 4px; }}
 .kopf {{ display:flex; justify-content:space-between; align-items:baseline; gap:12px; }}
 .kopf a.neu {{ color:var(--accent); font-size:.9rem; white-space:nowrap; border:1px solid var(--accent); border-radius:6px; padding:3px 10px; }}
 .stand {{ color:var(--muted); font-size:.9rem; margin-bottom:16px; }}
 .chips {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:24px; }}
 .chip {{ border:1px solid var(--accent); background:transparent; color:var(--accent); border-radius:999px; padding:4px 12px; cursor:pointer; font:inherit; font-size:.9rem; }}
 .chip.an {{ background:var(--accent); color:var(--bg); }}
 .datum {{ display:flex; flex-wrap:wrap; gap:8px 16px; align-items:center; margin:-8px 0 12px; }}
 .datum label {{ display:flex; gap:6px; align-items:center; color:var(--muted); font-size:.9rem; }}
 .datum input {{ font:inherit; font-size:.9rem; padding:3px 6px; border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--fg); }}
 .schnell {{ display:flex; flex-wrap:wrap; gap:6px; margin-bottom:8px; }}
 .schnell button {{ border:1px solid var(--line); background:transparent; color:var(--fg); border-radius:6px; padding:3px 10px; cursor:pointer; font:inherit; font-size:.85rem; }}
 .schnell button.an {{ border-color:var(--accent); color:var(--accent); font-weight:600; }}
 .anzahl {{ color:var(--muted); font-size:.85rem; margin-bottom:8px; }}
 h2 {{ font-size:1rem; margin:24px 0 6px; padding-bottom:4px; border-bottom:1px solid var(--line); }}
 [hidden] {{ display:none !important; }}
 .veraltet {{ background:var(--accent); color:var(--bg); border-radius:8px; padding:8px 12px; margin:8px 0; font-size:.9rem; }}
 .hinweis {{ color:var(--muted); font-size:.85rem; margin:-16px 0 16px; }}
 ul {{ list-style:none; margin:0; padding:0; }}
 li {{ display:grid; grid-template-columns:3.5rem 1fr auto; gap:12px; padding:6px 0; align-items:baseline; }}
 .zeit {{ font-variant-numeric:tabular-nums; color:var(--muted); }}
 a {{ color:var(--fg); text-decoration:none; }} a:hover {{ color:var(--accent); text-decoration:underline; }}
 .ort {{ color:var(--muted); font-size:.85rem; text-align:right; }}
 details {{ margin-top:40px; color:var(--muted); font-size:.85rem; }}
 @media (max-width:520px) {{ li {{ grid-template-columns:3rem 1fr; }} .ort {{ grid-column:2; text-align:left; }} }}
</style></head><body>
<div class="kopf"><h1>Mein Spielplan</h1>{
    f'<a class="neu" href="{html.escape(formular_url)}" target="_blank" rel="noopener">+ Spielstätte</a>'
    if formular_url else ""}</div>
<div class="veraltet" id="veraltet" data-stand="{datetime.now(timezone.utc).isoformat()}" hidden></div>
<div class="stand">Stand: {datetime.now(BERLIN):%d.%m.%Y, %H:%M} Uhr · {len(termine)} Termine</div>
<div class="chips">{"".join(knopf(o) for o in orte)}</div>
<div class="hinweis" id="hinweis">Alle Orte. Tippe einen oder mehrere Orte an, um nur diese zu sehen.</div>
<div class="schnell">
 <button data-schnell="alle" class="an">Alle</button><button data-schnell="heute">Heute</button>
 <button data-schnell="morgen">Morgen</button><button data-schnell="wochenende">Wochenende</button>
 <button data-schnell="7">Nächste 7 Tage</button><button data-schnell="30">Nächste 30 Tage</button>
</div>
<div class="datum">
 <label>Von <input type="date" id="von" min="{min(tage).isoformat() if tage else ''}" max="{max(tage).isoformat() if tage else ''}"></label>
 <label>Bis <input type="date" id="bis" min="{min(tage).isoformat() if tage else ''}" max="{max(tage).isoformat() if tage else ''}"></label>
</div>
<div class="anzahl" id="anzahl"></div>
{"".join(abschnitte) or "<p>Keine Termine gefunden.</p>"}
<details><summary>Abruf-Protokoll</summary><ul>{status}</ul></details>
<script>
const von = document.getElementById('von'), bis = document.getElementById('bis');
const iso = d => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
const plus = (d, n) => {{ const x = new Date(d); x.setDate(x.getDate() + n); return x; }};

function filtern() {{
  const an = new Set([...document.querySelectorAll('.chip.an')].map(x => x.dataset.ort));
  const alleOrte = an.size === 0;  // nichts angetippt = alle Orte zeigen
  document.getElementById('hinweis').textContent = alleOrte
    ? 'Alle Orte. Tippe einen oder mehrere Orte an, um nur diese zu sehen.'
    : 'Nur ausgewählte Orte. Erneut antippen zum Abwählen.';
  let zahl = 0;
  document.querySelectorAll('section').forEach(s => {{
    const d = s.dataset.datum;
    const imZeitraum = (!von.value || d >= von.value) && (!bis.value || d <= bis.value);
    s.querySelectorAll('li[data-ort]').forEach(li => {{
      li.hidden = !(imZeitraum && (alleOrte || an.has(li.dataset.ort)));
      if (!li.hidden) zahl++;
    }});
    s.hidden = !s.querySelector('li:not([hidden])');
  }});
  document.getElementById('anzahl').textContent = zahl === 1 ? '1 Termin' : zahl + ' Termine';
}}

function schnell(art) {{
  const heute = new Date();
  let a = '', b = '';
  if (art === 'heute') {{ a = b = iso(heute); }}
  else if (art === 'morgen') {{ a = b = iso(plus(heute, 1)); }}
  else if (art === 'wochenende') {{
    const tag = heute.getDay();                     // 0 = So, 5 = Fr, 6 = Sa
    const start = (tag === 0 || tag === 6) ? heute : plus(heute, 5 - tag);
    a = iso(start); b = iso(plus(heute, tag === 0 ? 0 : 7 - tag));
  }}
  else if (art !== 'alle') {{ a = iso(heute); b = iso(plus(heute, Number(art) - 1)); }}
  von.value = a; bis.value = b;
  document.querySelectorAll('[data-schnell]').forEach(k => k.classList.toggle('an', k.dataset.schnell === art));
  filtern();
}}

document.querySelectorAll('.chip').forEach(c => c.onclick = () => {{ c.classList.toggle('an'); filtern(); }});
document.querySelectorAll('[data-schnell]').forEach(k => k.onclick = () => schnell(k.dataset.schnell));
[von, bis].forEach(f => f.onchange = () => {{
  document.querySelectorAll('[data-schnell]').forEach(k => k.classList.remove('an'));
  filtern();
}});
filtern();
// Warnung, wenn die Daten älter als 2 Tage sind (z. B. weil die tägliche Aktualisierung stockt)
(() => {{
  const v = document.getElementById('veraltet');
  const tage = Math.floor((Date.now() - new Date(v.dataset.stand)) / 86400000);
  if (tage >= 2) {{
    v.textContent = 'Achtung: Diese Daten sind ' + tage + ' Tage alt. Termine können sich geändert haben.';
    v.hidden = false;
  }}
}})();
{APP_SKRIPT if app else ""}
</script>
</body></html>"""
    pfad.write_text(seite, encoding="utf-8")


BROWSER_PFADE = {
    "chrome": [
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    ],
    "firefox": [
        r"%ProgramFiles%\Mozilla Firefox\firefox.exe",
        r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe",
        "/Applications/Firefox.app/Contents/MacOS/firefox",
        "firefox",
    ],
    "edge": [
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
        "microsoft-edge",
    ],
}


def oeffne_im_browser(uri):
    """Öffnet die Übersicht im Browser aus einstellungen.json ("browser": "chrome" / "firefox" /
    "edge" oder ein kompletter Pfad). Ohne Angabe oder wenn er nicht gefunden wird: Standardbrowser."""
    wunsch = ""
    datei = HIER / "einstellungen.json"
    if datei.exists():
        try:
            wunsch = str(json.loads(datei.read_text(encoding="utf-8")).get("browser", "")).strip()
        except (ValueError, AttributeError):
            print("Hinweis: einstellungen.json ist fehlerhaft, nutze den Standardbrowser.", file=sys.stderr)
    if wunsch:
        for kandidat in BROWSER_PFADE.get(wunsch.lower(), [wunsch]):
            pfad = os.path.expandvars(kandidat)
            programm = pfad if os.path.isfile(pfad) else shutil.which(pfad)
            if programm and "%" not in programm:
                subprocess.Popen([programm, uri])
                return
        print(f"Hinweis: Browser '{wunsch}' nicht gefunden, nutze den Standardbrowser.", file=sys.stderr)
    webbrowser.open(uri)


def main():
    global DEBUG
    p = argparse.ArgumentParser(description="Sammelt Spielpläne deiner Lieblingsorte.")
    p.add_argument("--tage", type=int, default=120, help="wie viele Tage im Voraus (Standard: 120)")
    p.add_argument("--orte", default=str(HIER / "orte.json"), help="Pfad zur Orte-Liste")
    p.add_argument("--ausgabe", default=str(HIER / "spielplan.html"), help="Ziel-HTML-Datei")
    p.add_argument("--nicht-oeffnen", action="store_true", help="HTML nicht automatisch im Browser öffnen")
    p.add_argument("--debug", action="store_true", help="Details zu jedem Abruf anzeigen")
    p.add_argument("--app", action="store_true", help="Übersicht als installierbare Web-App erzeugen (GitHub Pages)")
    args = p.parse_args()
    DEBUG = args.debug
    for strom in (sys.stdout, sys.stderr):  # Windows: Umlaute/Pfeile auch bei Umleitung in Datei
        try:
            strom.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    orte = json.loads(Path(args.orte).read_text(encoding="utf-8"))
    heute = date.today()
    bis = heute + timedelta(days=args.tage)

    alle, berichte = {}, []
    for ort in orte:
        print(f"→ {ort['name']} …", file=sys.stderr)
        termine, methode = sammle_ort(ort, bis)
        termine = [t for t in termine if heute <= t.datum <= bis]
        if ort.get("nur_spielort"):  # z. B. "Stralsund": nur Termine, deren Spielort das enthält
            filter_text = ort["nur_spielort"].lower()
            termine = [t for t in termine if filter_text in (t.spielort or t.titel).lower()]
        for t in termine:
            alle.setdefault(t.schluessel, t)
        if methode:
            bericht = f"{len(termine)} Termine bis {bis:%d.%m.%Y} (Methode: {methode})"
        else:
            bericht = "keine Termine gefunden. Tipp: in orte.json 'programm_url' oder 'ical' angeben, --debug nutzen"
        berichte.append((ort["name"], bericht))
        print(f"  {bericht}", file=sys.stderr)

    termine = sorted(alle.values(), key=lambda t: (t.datum, t.uhrzeit or "99", t.ort))
    for t in termine:
        print(f"{WOCHENTAGE[t.datum.weekday()]} {t.datum:%d.%m.} {t.uhrzeit or '     '}  {t.ort:<22} {t.titel}")

    ziel = Path(args.ausgabe)
    repo = os.environ.get("GITHUB_REPOSITORY", "")  # auf GitHub automatisch gesetzt, z. B. "name/spielplan"
    formular = f"https://github.com/{repo}/issues/new?template=neue-spielstaette.yml" if repo else ""
    schreibe_html(termine, ziel, berichte, app=args.app, formular_url=formular)
    print(f"\nÜbersicht gespeichert: {ziel}", file=sys.stderr)
    if not args.nicht_oeffnen:
        oeffne_im_browser(ziel.resolve().as_uri())


if __name__ == "__main__":
    main()
