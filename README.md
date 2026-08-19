# CKO KNX IBS Scanner

Lokaler Windows-Assistent für KNX-Inbetriebnahme, ETS-Projektvergleich,
KNX/IP-Diagnose und Abschlussprotokolle.

## Stand v0.1

Der erste Prototyp bietet:

- lokales Windows-WebView2-Fenster im CKO-Design (kein separates Browserfenster)
- automatische Suche nach KNXnet/IP-Schnittstellen
- Anzeige von Tunnelling-, Routing- und Secure-Eigenschaften
- lokaler Import von ETS-4/5/6-Projekten (`.knxproj`)
- erste grafische Projekt- und Topologieübersicht
- Windows-Installer mit CKO-Programmsymbol, Startmenü und Deinstallation

## Windows-Signatur

Der Build ist für Authenticode-Code-Signing vorbereitet. Sobald ein gültiges
Code-Signing-Zertifikat als GitHub-Secret hinterlegt ist, werden Anwendung und
Installer automatisch signiert. Zertifikat und Passwort werden niemals im
öffentlichen Repository gespeichert.

Alle Projekt- und Verbindungsdaten werden ausschließlich auf dem Rechner des
Benutzers verarbeitet. Die Weboberfläche ist nur an `127.0.0.1` gebunden.

## Lokal starten

Voraussetzung: Python 3.12 oder neuer.

```bash
python -m venv .venv
.venv/bin/pip install -e .[dev]
.venv/bin/python launcher.py
```

Unter Windows lauten die letzten beiden Befehle:

```powershell
.venv\Scripts\pip install -e .[dev]
.venv\Scripts\python launcher.py
```

## Open Source

Dieses Projekt steht unter GPL-3.0. Es nutzt unter anderem
[xknx](https://github.com/XKNX/xknx) und
[xknxproject](https://github.com/XKNX/xknxproject). Die technische Umsetzung
orientiert sich an [SpectrumKNX](https://github.com/martinhoefling/SpectrumKNX),
einem freien KNX-Telegramm- und Analysewerkzeug.
