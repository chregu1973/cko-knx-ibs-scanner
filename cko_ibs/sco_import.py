"""Import von ETS-Aufzeichnungen und Auswertung der 6-Byte-SCO-Telegramme.

Unterstützt:
* ETS-Busmonitor/Gruppenmonitor «Telegramme speichern» als XML (CommunicationLog, cEMI in RawData)
* ETS-Gruppenmonitor als CSV (Tabulator, Semikolon oder Komma; Spalte RawData mit cEMI-Hex,
  sonst Info/Source/Destination)

Übernommen werden nur Gruppentelegramme (Schreiben/Antwort) mit genau 6 Datenbyte. Alles wird lokal
verarbeitet.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from typing import Any
from xml.etree import ElementTree

from cko_ibs import sco

MAX_IMPORT_BYTES = 50 * 1024 * 1024
APCI_NAMES = {0x080: "GroupValueWrite", 0x040: "GroupValueResponse"}
SERVICE_DIRECTION = {"L_Data.ind": "Incoming", "L_Data.con": "Outgoing", "L_Data.req": "Outgoing",
                     "vom bus": "Incoming", "zum bus": "Outgoing", "from bus": "Incoming", "to bus": "Outgoing"}


class ImportError_(ValueError):
    """Datei kann nicht als ETS-Aufzeichnung gelesen werden."""


def _group_address(high: int, low: int) -> str:
    return f"{high >> 3}/{high & 0x07}/{low}"


def _individual_address(high: int, low: int) -> str:
    return f"{high >> 4}.{high & 0x0F}.{low}"


def parse_cemi(raw: bytes) -> dict[str, Any] | None:
    """cEMI-L_Data-Frame zerlegen; None, wenn es kein Gruppentelegramm mit Daten ist."""
    if len(raw) < 11:
        return None
    message_code, additional_length = raw[0], raw[1]
    offset = 2+additional_length
    if len(raw) < offset+9:
        return None
    control2 = raw[offset+1]
    source = _individual_address(raw[offset+2], raw[offset+3])
    if not control2 & 0x80:  # Einzeladresse als Ziel (Gerätezugriff): nicht relevant
        return None
    destination = _group_address(raw[offset+4], raw[offset+5])
    length = raw[offset+6]
    apdu = raw[offset+7:]
    if len(apdu) < 2 or length < 1:
        return None
    apci = ((apdu[0] & 0x03) << 8) | apdu[1]
    service = APCI_NAMES.get(apci & 0x3C0)
    if service is None:
        return None
    data = apdu[2:2+length-1] if length > 1 else bytes([apdu[1] & 0x3F])
    return {"message_code": message_code, "source": source, "destination": destination,
            "service": service, "data": data}


def _decode_text(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportError_("Datei ist weder UTF-8, UTF-16 noch Windows-1252 kodiert.")


def _read_xml(text: str) -> list[dict[str, Any]]:
    if re.search(r"<!DOCTYPE|<!ENTITY", text[:4096], re.IGNORECASE):
        raise ImportError_("XML mit DOCTYPE/ENTITY wird aus Sicherheitsgründen nicht gelesen.")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise ImportError_(f"XML ist nicht lesbar: {exc}") from exc
    frames = []
    for element in root.iter():
        if not element.tag.endswith("Telegram") or not element.get("RawData"):
            continue
        frames.append({"time": _parse_xml_time(element.get("Timestamp", "")),
                       "service": element.get("Service", ""), "raw": element.get("RawData", "")})
    return frames


def _parse_xml_time(value: str) -> str:
    """ETS schreibt UTC mit 7 Nachkommastellen (…T20:23:24.0111375Z); Anzeige in Ortszeit."""
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})?", value.strip())
    if not match:
        return value
    base, fraction, zone = match.groups()
    iso = f"{base}.{(fraction or '0')[:6].ljust(6, '0')}{'+00:00' if zone in (None, 'Z') else zone}"
    try:
        return datetime.fromisoformat(iso).astimezone().isoformat(timespec="milliseconds")
    except ValueError:
        return value


def _parse_csv_time(value: str) -> str:
    value = value.strip()
    for pattern in ("%d.%m.%Y %H:%M:%S,%f", "%d.%m.%Y %H:%M:%S.%f", "%d.%m.%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            # ETS speichert die Ortszeit des PCs ohne Zone
            return datetime.strptime(value, pattern).astimezone().isoformat(timespec="milliseconds")
        except ValueError:
            continue
    return value


def _read_csv(text: str) -> list[dict[str, Any]]:
    first_line = text.splitlines()[0] if text else ""
    delimiter = max("\t;,", key=first_line.count)
    rows = list(csv.DictReader(io.StringIO(text), delimiter=delimiter))
    if not rows:
        raise ImportError_("Die CSV-Datei enthält keine Telegramme.")
    columns = {name.strip().lower(): name for name in rows[0] if name}
    if "rawdata" not in columns and "info" not in columns:
        raise ImportError_("CSV braucht die Spalte «RawData» (ETS-Gruppenmonitor) oder «Info».")
    frames = []
    for row in rows:
        time = _parse_csv_time(row.get(columns.get("time", ""), "") or "")
        raw = (row.get(columns.get("rawdata", ""), "") or "").replace(" ", "")
        if raw and re.fullmatch(r"[0-9A-Fa-f]+", raw):
            frames.append({"time": time, "service": row.get(columns.get("service", ""), ""), "raw": raw})
            continue
        info = row.get(columns.get("info", ""), "") or ""
        match = re.match(r"\s*((?:[0-9A-Fa-f]{2} ){5}[0-9A-Fa-f]{2})\b", info)
        if match:
            frames.append({"time": time, "service": row.get(columns.get("service", ""), ""),
                           "decoded_data": bytes.fromhex(match.group(1).replace(" ", "")),
                           "source": row.get(columns.get("sourceaddress", ""), ""),
                           "destination": row.get(columns.get("destinationaddress", ""), "")
                           or row.get(columns.get("destination", ""), "")})
    return frames


def read_recording(filename: str, raw: bytes) -> list[dict[str, Any]]:
    """Datei lesen und die enthaltenen Frames liefern (noch ohne Filter)."""
    if len(raw) > MAX_IMPORT_BYTES:
        raise ImportError_("Die Aufzeichnung ist grösser als 50 MB.")
    text = _decode_text(raw)
    stripped = text.lstrip()
    if stripped.startswith("<"):
        return _read_xml(stripped)
    if filename.lower().endswith(".xml"):
        raise ImportError_("Die XML-Datei beginnt nicht mit einem XML-Element.")
    return _read_csv(text)


def sco_entries(filename: str, frames: list[dict[str, Any]], group_names: dict[str, str | None]) -> dict[str, Any]:
    """Nur Gruppentelegramme mit 6 Datenbyte übernehmen und als Mitschnitt-Einträge dekodieren."""
    entries, group_telegrams = [], 0
    origin = f"Import: {filename}"
    for frame in frames:
        if "raw" in frame:
            try:
                telegram = parse_cemi(bytes.fromhex(frame["raw"]))
            except ValueError:
                telegram = None
            if telegram is None:
                continue
        else:
            telegram = {"source": frame.get("source", ""), "destination": frame.get("destination", ""),
                        "service": "GroupValueWrite", "data": frame["decoded_data"]}
        service_name = str(frame.get("service", "")).strip()
        direction = SERVICE_DIRECTION.get(service_name) or SERVICE_DIRECTION.get(service_name.lower(), "Incoming")
        group_telegrams += 1
        if len(telegram["data"]) != 6:
            continue
        decoded = sco.decode(telegram["data"])
        entries.append({
            "time": frame["time"], "direction": direction, "source": telegram["source"],
            "destination": telegram["destination"], "group_name": group_names.get(telegram["destination"]),
            "service": telegram["service"], "secure": False, "hex": decoded["hex"], "decoded": decoded,
            "origin": origin,
        })
    return {"frames": len(frames), "group_telegrams": group_telegrams, "entries": entries}
