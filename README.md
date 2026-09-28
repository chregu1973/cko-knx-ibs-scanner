# CKO KNX IBS Scanner

Lokaler Windows-Assistent für KNX-Inbetriebnahme, ETS-Projektvergleich,
KNX/IP- und KNX-USB-Diagnose.

## Version 0.9.3

- **Dekodierung nach der ETS-App (GPA) abgeglichen:** Wipp Auf/Ab (Wippdauer vom Aktor) und Stopp als Fahrbefehle, «Lokalbedienung sperren/freigeben», Lamellenwinkel-Korrekturfaktor, «Grenzen Sicherheit», «Grenzen Sicherheit/Automatik Lokalbedienung», Busüberwachung aktiv/inaktiv.
- **Sperre als Maske:** P1 = betroffene Sperren, P2 = gesetzte Sperren. Ungültige Kombinationen (z. B. `01 10 01 02`) werden als «Sperre unbekannt» angezeigt. Die Fahrbefehlssperre setzt jetzt `01 10 01 01` (noch am Bus zu bestätigen).
- **Neue Masken:** Wipp Auf, Wipp Ab und Stopp bei der Beschattungsposition.
- **Beenden:** Das Programmfenster wird direkt aus der Anwendung geschlossen und hängt nicht mehr an der JavaScript-Brücke.

## Version 0.9.2

- **Export JSON/CSV** speichert den Mitschnitt direkt im **Downloads-Ordner** des angemeldeten Benutzers (auch bei verschobenem Downloads-Ordner). Der Pfad wird angezeigt, «Im Explorer anzeigen» markiert die Datei. Das Programmfenster blockiert Browser-Downloads, deshalb speichert der lokale Dienst die Datei selbst.

## Version 0.9.1

- **Sicherheit wie die Zentrale:** Nach einem Mitschnitt einer Beschattungszentrale korrigiert. Die Sperre trägt keine Priorität (P1 = Sperrart, P2 = 02 zum Setzen, 00 zum Löschen). Setzen = Sperre, danach Fahrbefehl mit Warn-, Sicherheits- oder Gefahrenpriorität; Aufheben = Fahrbefehlssperre und Tastensperre einzeln löschen. Die Telegramme der Zentrale werden Byte für Byte nachgebildet.
- Am Bus mit der ETS-App geprüft: Fahrbefehl (Sektor, Priorität, Fixposition) und lokale Bedienung.

## Version 0.9.0 – SCO-Objekt (6 Byte)

Neue Seite **SCO-Objekt** für das proprietäre SCO-Objekt (6 Byte, «SunControl Object»):

- **Testmaske:** Gruppenadresse, einzelner Sektor (1–512) oder Sektorgruppe; Beschattungsposition (obere/untere Endlage, Fixposition P1–P4) mit Grenz-, Automatik- oder Prioritätsbefehl; lokale oder Gruppenbedienung; Rohtelegramm (Replay aus dem Mitschnitt).
- **Sicherheit für den Sektor:** Setzen = Fahrbefehl mit Warn-, Sicherheits- oder Gefahrenpriorität + Sperre aktiv; Aufheben = Sperre passiv mit gleicher Priorität und Sperrart. Nur nach ausdrücklicher Freigabe. Ein Banner zeigt von hier gesetzte Sperren mit «Aufheben», bis sie gelöscht sind.
- Jede Sendung erst nach **Vorschau** (Hex und Klartext) und Bestätigung; gesendet wird als GroupValueWrite mit genau 6 Datenbyte über die bestehende KNX-Verbindung.
- **Mitschnitt und Datenauflösung:** SCO-Telegramme auf markierten GAs und auf GAs ohne Standard-DPT werden dekodiert (Sektor, Befehl, Priorität, Aktion, P1–P4). «= Vorschau» markiert Byte-gleiche Telegramme – so lassen sich eigene Befehle mit denen der Beschattungszentrale (z. B. im Simulationsbetrieb) vergleichen.
- **Sektorübersicht** je GA, Sektor und Quelle mit Befehlen und Prioritäten; Export des Mitschnitts als JSON oder CSV.
- Belegung nach Flow v3 (hbTec) und KNXUltimate `dpt60001`.

## Version 0.8.2

Version 0.8.2 verhindert veraltete WebView-Inhalte nach einem Update. Die Oberfläche wird immer aus der aktuell installierten Version geladen; die Versionsnummer bleibt direkt in der Sidebar sichtbar.
Der frühere externe Referenzverweis ist vollständig entfernt.

Version 0.8.0 ergänzt eine gezielte Linienauswahl für die KNX-Geräteprüfung und
hebt den ohne ETS-Projekt nutzbaren Linienscan deutlicher hervor. Der native
Windows-Schließablauf wurde zusätzlich abgesichert: Nach dem sauberen Trennen der
KNX-Verbindung wird das WebView-Fenster asynchron geschlossen; ein Watchdog beendet
eine blockierte WebView-Instanz.

Neu: Der KNX-Gruppenmonitor hat eine eigene, übersichtliche Seite. Die Aufzeichnung
kann ohne Trennung der KNX-Verbindung gestoppt und anschließend nach Gruppenadresse,
GA-Bezeichnung, Quelle, Dienst oder Secure-Telegrammen gefiltert werden.

Nach einem physikalischen Adressscan kann eine gefundene Adresse ausgewählt
und über **Gerätedaten lesen** abgefragt werden. Das Tool liest – soweit vom Gerät
unterstützt – Maskenversion, Herstellerkennung, Seriennummer, Firmware,
Programmversion, Bestellinformation, Objektname und PEI-Typ. Nicht jedes KNX-Gerät
stellt alle standardisierten Management-Eigenschaften bereit; fehlende Angaben
werden deshalb nicht als Gerätefehler bewertet.

- Direkte KNX-USB-Verbindung über USB-HID/cEMI
- Siemens OCI702 USB als erstes Referenzmodell
- lokale USB-Erkennung mit Gerätekennung und Seriennummer
- Gruppenmonitor und Geräteprüfung über dieselbe USB-Verbindung
- wählbare physikalische Quelladresse für linienübergreifende Diagnose
- noch nicht implementierte Sidebar-Bereiche sind als „folgt“ deaktiviert
- USB-Verbindungen erfordern zwingend eine freie physikalische Quelladresse
- eigenständiger Linienscan nach belegten physikalischen Adressen ohne ETS-Projekt
- KNX-Verbindung separat trennen, ohne die Anwendung zu beenden
- verständliche Diagnose, wenn die lokale KNX-Busbestätigung ausbleibt

Vor dem Öffnen der OCI702 muss die ETS-Verbindung zur USB-Schnittstelle getrennt werden.
Eine USB-Schnittstelle kann normalerweise nicht gleichzeitig von ETS und dem IBS Scanner
verwendet werden.

## Funktionsumfang

Der erste Prototyp bietet:

- lokales Windows-WebView2-Fenster im CKO-Design (kein separates Browserfenster)
- automatische Suche nach KNXnet/IP-Schnittstellen
- Anzeige und optionale Anforderung der physikalischen KNX-Tunneladresse
- Diagnosetest vor dem vollständigen Gerätescan
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
[xknxproject](https://github.com/XKNX/xknxproject).