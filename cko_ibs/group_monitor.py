"""Gruppenmonitor: Werte kodieren und lesbar machen, Schalt-/Status-Adressen zuordnen, Funktionstests auswerten."""

from __future__ import annotations

import dataclasses
import enum
import math
import re
import unicodedata
from datetime import date, datetime, time
from typing import Any

from xknx.dpt import DPTArray, DPTBase, DPTBinary
from xknx.exceptions import ConversionError, CouldNotParseTelegram
from xknx.telegram.address import GroupAddress

SEND_MODE_SECONDS = 30 * 60

# Wörter, die eine Rückmelde-/Status-Adresse kennzeichnen
STATUS_WORDS = {"status", "rueckmeldung", "rm", "feedback", "zustand", "ist", "istwert", "state", "stat",
                "rueckmeldewert", "statuswert"}
# Funktionswörter, die bei der Zuordnung keine Rolle spielen
FUNCTION_WORDS = {"schalten", "ein", "aus", "einaus", "dimmen", "dimmwert", "wert", "helligkeit", "helligkeitswert",
                  "position", "fahren", "auf", "ab", "aufab", "stopp", "stop", "lamelle", "lamellen", "absolut",
                  "relativ", "switch", "dim", "value", "brightness", "move", "objekt", "ga"}
# Reine Melder: nie als «Befehl» für den Funktionstest vorschlagen (Kontakte, Sensoren, Alarme, Messwerte)
SENSOR_WORDS = re.compile(r"kontakt|melder|sensor|alarm|praesenz|bewegung|wetter|wind|regen|stoerung|fehler|"
                          r"messwert|istwert|aussentemp|zaehler|verbrauch|leistung|energie|lux|co2|feuchte")
# Datentypen, die bedient werden (Schalten, Dimmen, Storen, Szenen, Betriebsart, Farbe, Sollwerte)
CONTROL_MAINS = {1, 3, 5, 9, 17, 18, 20, 232, 251}
SENSOR_DPTS = {"1.019", "1.005", "1.006", "1.020"}  # Fenster/Tür, Alarm, Binärwert, Logik-Status
# Welche Datentypen als Rückmeldung zu einem Befehl passen (Hauptnummer Befehl → Hauptnummern Status)
COMPATIBLE_STATUS = {1: {1, 5}, 3: {5}, 5: {5}, 9: {9}, 14: {14}, 7: {7}, 8: {8}, 12: {12}, 13: {13},
                     17: {17}, 18: {17, 18}, 20: {20}, 232: {232}, 251: {251}}


class MonitorError(ValueError):
    """Ungültige Eingabe für den Gruppenmonitor."""


def parse_group_address(value: Any) -> str:
    try:
        return str(GroupAddress(str(value).strip()))
    except Exception as exc:
        raise MonitorError(f"Ungültige Gruppenadresse «{value}» – Format z. B. 1/2/3.") from exc


def dpt_main(dpt: Any) -> int | None:
    match = re.match(r"\s*(?:DPST-|DPT-)?(\d+)", str(dpt or ""))
    return int(match.group(1)) if match else None


def transcoder_for(dpt: Any):
    if not dpt:
        return None
    try:
        return DPTBase.parse_transcoder(str(dpt))
    except (ConversionError, ValueError, KeyError, TypeError):
        return None


def json_value(value: Any) -> Any:
    """Dekodierten xknx-Wert JSON-tauglich machen (Enums, Datenklassen, Zeiten)."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, enum.Enum):
        # 1-Bit-Typen (Switch, UpDown, OpenClose …) als true/false; die Bedeutung kennt die Oberfläche über den DPT
        return value.value if isinstance(value.value, bool) else value.name.lower()
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if dataclasses.is_dataclass(value):
        return {key: json_value(item) for key, item in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return str(value)


def payload_hex(payload_value: Any) -> str:
    if isinstance(payload_value, DPTBinary):
        return f"{payload_value.value:02X}"
    if isinstance(payload_value, DPTArray):
        return " ".join(f"{byte:02X}" for byte in payload_value.value)
    return ""


# xknx liefert einzelne Einheiten auf Englisch
UNIT_DE = {"counter pulses": "Impulse", "pulses": "Impulse"}


def unit_of(transcoder: Any) -> str | None:
    unit = getattr(transcoder, "unit", None) if transcoder is not None else None
    return UNIT_DE.get(unit, unit)


def describe(dpt: Any) -> dict[str, Any]:
    transcoder = transcoder_for(dpt)
    return {
        "main": dpt_main(dpt),
        "unit": unit_of(transcoder),
        "value_type": getattr(transcoder, "value_type", None) if transcoder else None,
    }


# 1-Bit-Wörter je Untertyp: True-Wörter / False-Wörter (1.008: True = Ab, 1.009: True = Schliessen)
BOOL_WORDS = {
    8: ({"ab", "runter", "down", "1", "true"}, {"auf", "hoch", "up", "0", "false"}),
    9: ({"zu", "schliessen", "schließen", "geschlossen", "close", "1", "true"},
        {"offen", "oeffnen", "öffnen", "open", "0", "false"}),
}
GENERIC_TRUE = {"1", "true", "ein", "an", "on", "ja", "freigeben", "aktiv"}
GENERIC_FALSE = {"0", "false", "aus", "off", "nein", "sperren", "inaktiv"}


def _dpt_sub(dpt: Any) -> int | None:
    match = re.match(r"\s*(?:DPST-|DPT-)?\d+[.-](\d+)", str(dpt or ""))
    return int(match.group(1)) if match else None


def _coerce(value: Any, main: int | None, sub: int | None = None) -> Any:
    """Eingaben aus der Oberfläche («ein», «auf», «50», «21,5») in den passenden Python-Typ wandeln."""
    if isinstance(value, str):
        text = value.strip()
        lowered = text.lower()
        if main == 1:
            true_words, false_words = BOOL_WORDS.get(sub or 0, (GENERIC_TRUE, GENERIC_FALSE))
            if lowered in true_words:
                return True
            if lowered in false_words:
                return False
        number = text.replace("'", "").replace(",", ".")
        if re.fullmatch(r"[-+]?\d+", number):
            return int(number)
        if re.fullmatch(r"[-+]?\d*\.\d+(e[-+]?\d+)?|[-+]?\d+e[-+]?\d+", number, re.IGNORECASE):
            return float(number)
        return text
    return value


def encode(dpt: Any, value: Any, raw_hex: str | None = None,
           raw_small: bool = False) -> tuple[DPTBinary | DPTArray, Any]:
    """Wert für GroupValueWrite kodieren. Ohne bekannten Datentyp als Rohwert: kurz (bis 6 Bit) oder als Bytes."""
    if raw_hex:
        cleaned = re.sub(r"[^0-9a-fA-F]", "", raw_hex)
        if not cleaned or len(cleaned) % 2 and not raw_small or len(cleaned) > 28:
            raise MonitorError("Rohwert als Hex-Bytes angeben, z. B. «01» oder «0C 1A».")
        if raw_small:
            number = int(cleaned, 16)
            if number > 0x3F:
                raise MonitorError("Ein kurzer Rohwert (1–6 Bit) liegt zwischen 00 und 3F.")
            return DPTBinary(number), f"{number:02X}"
        data = bytes.fromhex(cleaned)
        return DPTArray(tuple(data)), " ".join(f"{byte:02X}" for byte in data)
    transcoder = transcoder_for(dpt)
    if transcoder is None:
        raise MonitorError("Datentyp (DPT) unbekannt – DPT festlegen oder einen Rohwert in Hex senden.")
    coerced = _coerce(value, dpt_main(dpt), _dpt_sub(dpt))
    try:
        return transcoder.to_knx(coerced), coerced
    except (ConversionError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise MonitorError(f"Wert «{value}» passt nicht zu DPT {dpt}: {exc}") from exc


# Datentyp erraten, wenn weder Gruppenadresse noch Kommunikationsobjekt einen haben (wie die ETS über die Länge)
_GUESS_BY_NAME = {
    1: [(r"winkel|lamelle", "5.003"), (r"szene", "17.001"), (r"betriebsart|modus|hvac", "20.102"),
        (r"%|prozent|helligkeit|position|stellwert|dimm|wert", "5.001")],
    2: [(r"lux|helligkeit|lichtwert|daemmerung|dammerung", "9.004"), (r"°c|grad|temp", "9.001"),
        (r"km/h|kmh", "9.028"), (r"m/s|wind", "9.005"), (r"%|feuchte", "9.007"), (r"ppm|co2|co₂|luftqual", "9.008"),
        (r"\bkw\b", "9.024"), (r"\bpa\b|druck", "9.006")],
    3: [(r"zeit|uhr", "10.001"), (r"datum", "11.001"), (r"rgb|farbe", "232.600")],
    4: [(r"kwh", "13.013"), (r"\bwh\b", "13.010"), (r"zaehler|zähler|counter|impuls", "13.001"),
        (r"\bw\b|leistung|watt", "14.056"), (r"\bv\b|spannung", "14.027"), (r"\ba\b|strom", "14.019")],
    14: [(r".", "16.001")],
}
# 1 Byte ohne Hinweis im Namen: meist Helligkeit/Position in % (Zählerwerte 0–255 sind selten)
_GUESS_BY_LENGTH = {1: "5.001", 2: "9", 4: "14"}


def guess_dpt(name: Any, payload_value: Any) -> str | None:
    """DPT anhand von Name/Einheit und Telegrammlänge vermuten (nur Anzeige, als «vermutet» gekennzeichnet)."""
    if isinstance(payload_value, DPTBinary):
        return "1.001"
    if not isinstance(payload_value, DPTArray):
        return None
    length = len(payload_value.value)
    text = " ".join(_words(str(name or ""))) + " " + str(name or "").lower()
    for pattern, dpt in _GUESS_BY_NAME.get(length, []):
        if re.search(pattern, text):
            return dpt
    return _GUESS_BY_LENGTH.get(length)


def value_text(value: Any, unit: str | None = None) -> str:
    """Kurztext für die Telegrammliste, wenn xknx selbst nichts dekodiert hat."""
    if isinstance(value, bool):
        return "1 · Ein/True" if value else "0 · Aus/False"
    if isinstance(value, float):
        value = round(value, 2)
    return f"{value} {unit}".strip() if unit and isinstance(value, (int, float)) else str(value)


def decode(dpt: Any, payload_value: Any) -> Any:
    transcoder = transcoder_for(dpt)
    if transcoder is None or payload_value is None:
        return None
    try:
        return json_value(transcoder.from_knx(payload_value))
    except (ConversionError, CouldNotParseTelegram, ValueError, TypeError):
        return None


# --- Zuordnung Befehl ↔ Rückmeldung -------------------------------------------------

def _words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue"))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return [word for word in re.split(r"[^a-z0-9]+", text) if word]


def _is_status(words: list[str]) -> bool:
    return any(word in STATUS_WORDS or word.startswith(("rueckmeld", "status")) for word in words)


def _core(words: list[str]) -> tuple[str, ...]:
    return tuple(word for word in words if word not in STATUS_WORDS and word not in FUNCTION_WORDS
                 and not word.startswith(("rueckmeld", "status")))


def _compatible(command_dpt: Any, status_dpt: Any) -> bool:
    command, status = dpt_main(command_dpt), dpt_main(status_dpt)
    if command is None or status is None:
        return True
    return status in COMPATIBLE_STATUS.get(command, {command})


def _parts(address: str) -> tuple[str, ...]:
    return tuple(str(address).split("/"))


def _is_command(entry: dict[str, Any]) -> bool:
    """Nur bedienbare Funktionen kommen als Befehl in Frage – Melder und Messwerte nicht."""
    dpt = entry["row"].get("dpt")
    main = dpt_main(dpt)
    words = " ".join(entry["own"])
    if SENSOR_WORDS.search(words):
        return False
    if dpt and re.sub(r"^DPS?T-?", "", str(dpt)).replace("-", ".") in SENSOR_DPTS:
        return False
    if main == 9 and not re.search(r"soll|setpoint|basis", words):
        return False  # Temperaturen usw. sind Messwerte, nur Sollwerte werden gesendet
    return main is None or main in CONTROL_MAINS


def status_pairs(rows: list[dict[str, Any]], range_names: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Vorschläge, welche Status-GA zu welcher Befehls-GA gehört (über Bezeichnungen und Gruppenstruktur)."""
    range_names = range_names or {}
    entries = []
    for row in rows:
        address = str(row.get("address") or "")
        parts = _parts(address)
        middle = range_names.get("/".join(parts[:2]), "") if len(parts) == 3 else ""
        own = _words(str(row.get("name") or ""))
        context = _words(middle)
        entries.append({"row": row, "address": address, "parts": parts, "own": own,
                        "status": _is_status(own) or _is_status(context),
                        "core": _core(own), "core_context": _core(own + context)})
    commands = [entry for entry in entries if not entry["status"] and _is_command(entry)]
    statuses = [entry for entry in entries if entry["status"]]
    pairs, used, paired = [], set(), set()
    # Zuerst gleiche Datentypen (Position ↔ Position), danach passende Kombinationen (Auf/Ab ↔ Position)
    for same_type_only in (True, False):
        for command in commands:
            if command["address"] in paired:
                continue
            best, best_score, reason = None, 0, ""
            for status in statuses:
                if status["address"] in used or not _compatible(command["row"].get("dpt"), status["row"].get("dpt")):
                    continue
                if same_type_only and dpt_main(command["row"].get("dpt")) != dpt_main(status["row"].get("dpt")):
                    continue
                same_name = bool(command["core"]) and command["core"] == status["core"]
                same_context = bool(command["core_context"]) and command["core_context"] == status["core_context"]
                if not (same_name or same_context):
                    continue
                same_main = command["parts"][:1] == status["parts"][:1]
                same_slot = len(command["parts"]) == 3 == len(status["parts"]) and same_main \
                    and command["parts"][2] == status["parts"][2]
                score = 3 * same_name + 2 * same_context + 2 * same_main + same_slot
                if score > best_score:
                    best, best_score = status, score
                    reason = ("gleiche Bezeichnung mit Status-Zusatz" if same_name and same_main else
                              "gleiche Untergruppe in der Status-Mittelgruppe" if same_slot else
                              "gleiche Bezeichnung inkl. Mittelgruppe" if same_context else "gleiche Bezeichnung")
            if best is not None:
                used.add(best["address"])
                paired.add(command["address"])
                pairs.append({"command": command["address"], "command_name": command["row"].get("name"),
                              "command_dpt": command["row"].get("dpt"), "status": best["address"],
                              "status_name": best["row"].get("name"), "status_dpt": best["row"].get("dpt"),
                              "reason": reason, "score": best_score})
    pairs.sort(key=lambda pair: tuple(int(part) if part.isdigit() else 0 for part in pair["command"].split("/")))
    return pairs


def same_kind(command_dpt: Any, status_dpt: Any) -> bool:
    """Gleicher Haupttyp: dann muss die Rückmeldung dem Befehl entsprechen (sonst, z. B. Dimmen → Helligkeit, genügt eine Antwort)."""
    command, status = dpt_main(command_dpt), dpt_main(status_dpt)
    return command is None or status is None or command == status


def values_match(sent: Any, received: Any, dpt: Any = None) -> bool:
    """Rückmeldung passt zum gesendeten Wert (Zahlen mit kleiner Toleranz, z. B. Dimmwert 50 % ↔ 49,8 %)."""
    if received is None:
        return False
    if isinstance(sent, bool) or isinstance(received, bool):
        return bool(sent) == bool(received)
    try:
        sent_number, received_number = float(sent), float(received)
    except (TypeError, ValueError):
        return str(sent).strip().lower() == str(received).strip().lower()
    tolerance = max(1.0, abs(sent_number) * 0.02) if dpt_main(dpt) == 5 else max(0.01, abs(sent_number) * 0.01)
    return abs(sent_number - received_number) <= tolerance


def range_names(group_ranges: dict[str, Any] | None) -> dict[str, str]:
    """Namen der Haupt- und Mittelgruppen aus xknxproject («1» → «Licht», «1/2» → «Status»)."""
    names: dict[str, str] = {}

    def walk(ranges: dict[str, Any]) -> None:
        for key, item in (ranges or {}).items():
            if isinstance(item, dict):
                if item.get("name"):
                    names[str(key)] = str(item["name"])
                walk(item.get("group_ranges") or {})

    walk(group_ranges or {})
    return names
