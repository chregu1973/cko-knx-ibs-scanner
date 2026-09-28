"""SCO-Objekt (6 Byte, «SunControl Object») kodieren und dekodieren.

Belegung nach Flow v3 (hbTec, 28.09.2026), KNXUltimate ``dpt60001`` und KNX-User-Forum:

* Byte 0 + Bit 0–1 von Byte 1: Sektorcode (10 Bit). Einzelner Sektor n → 2n−1, gerader Wert = Gruppe,
  das tiefste gesetzte Bit gibt die Gruppengrösse an.
* Byte 1, Bit 2–7: Command. Byte 2–5: Parameter P1–P4.
* Fahrbefehl (1): P1 Bit 5–7 Priorität, Bit 0–4 Funktion; P2 Fixposition 1–4.
* Sperre (4): P1 Bit 0–1 Sperrart; P2 = 0 Sperre löschen (passiv), sonst setzen (aktiv).
  ANNAHME: Die Priorität steht wie beim Fahrbefehl in P1 Bit 5–7. Am Bus mit der Zentrale bestätigen.
* Bedienung (5): P1 Bit 7 lokal (1) / Gruppe (0), Bit 0–6 Bedienart.

Das Objekt ist kein KNX-Standard-Datenpunkt. Unbekannte Werte werden roh gemeldet, nie geraten.
"""

from __future__ import annotations

import re
from typing import Any

COMMANDS = {
    1: "Fahrbefehl", 2: "Wertkorrektur", 3: "Automatikzustand", 4: "Sperre setzen/löschen",
    5: "Bedienung", 6: "Szene setzen", 7: "Spezialbefehl", 8: "Datum", 9: "Zeit synchronisieren",
    10: "Sensorwert-Meldung", 11: "Busüberwachung",
    16: "Fahrbereichsgrenzen Sicherheitsfahrbefehle", 17: "Fahrbereichsgrenzen Sicherheitsfahrbefehle",
    19: "Fahrbereichsgrenzen Sicherheitsfahrbefehle", 20: "Fahrbereichsgrenzen Sicherheitsfahrbefehle",
    22: "Fahrbereichsgrenzen Automatikfahrbefehle", 23: "Fahrbereichsgrenzen Automatikfahrbefehle",
    24: "Fahrbereichsgrenzen Automatikfahrbefehle",
}
PRIORITIES = {0: "Grenzbefehl", 1: "Automatikbefehl", 3: "Prioritätsbefehl", 4: "Warnbefehl",
              5: "Sicherheitsbefehl", 6: "Gefahrenbefehl"}
PRIORITY_KEYS = {"grenz": 0, "automatik": 1, "prioritaet": 3, "warn": 4, "sicherheit": 5, "gefahr": 6}
# Diese Prioritäten können Storen verriegeln und brauchen eine ausdrückliche Freigabe.
PROTECTED_PRIORITIES = {4, 5, 6}
DRIVE = {"keine": 0, "oben": 1, "unten": 2, "fix": 3}
DRIVE_NAMES = {0: "keine Fahrbewegung", 1: "obere Endlage", 2: "untere Endlage", 3: "Fixposition"}
OPERATION = ["lang auf", "lang ab", "kurz auf", "kurz ab", "Stopp", "lang-kurz auf", "lang-kurz ab"]
LOCKS = {"fahrbefehl": 1, "taste": 2, "beide": 3}
LOCK_NAMES = {1: "Fahrbefehlssperre", 2: "Tastensperre", 3: "Fahrbefehls- und Tastensperre"}


class SCOError(ValueError):
    """Ungültige Eingabe für das SCO-Objekt."""


def to_hex(data: bytes | list[int]) -> str:
    return " ".join(f"{value:02X}" for value in bytes(data))


def parse_hex(text: str) -> bytes:
    cleaned = re.sub(r"0x|\$|[\s,:;-]", "", str(text), flags=re.IGNORECASE)
    if not re.fullmatch(r"[0-9a-fA-F]{12}", cleaned):
        raise SCOError("Hex-Text muss genau 6 Byte enthalten, z. B. 47 04 63 04 00 00")
    return bytes.fromhex(cleaned)


def sector_code(sector: int | None = None, group: dict[str, int] | None = None) -> int:
    """Sektor 1–512 oder Gruppe {start, size} in den 10-Bit-Sektorcode umrechnen."""
    if group:
        size, start = int(group.get("size", 0)), int(group.get("start", 0))
        exponent = size.bit_length()-1
        if size < 2 or size > 512 or size != 1 << exponent:
            raise SCOError("Gruppengrösse muss 2, 4, 8 … 512 sein")
        if start < 1 or (start-1) % size:
            raise SCOError(f"Gruppenstart muss 1, {size+1}, {2*size+1} … sein")
        code = (((start-1)//size) << (exponent+1)) | (1 << exponent)
        if code > 0x3FF:
            raise SCOError("Sektorgruppe liegt ausserhalb des Sektorcodes")
        return code
    if sector is None or not 1 <= int(sector) <= 512:
        raise SCOError("Sektor muss 1–512 sein")
    return 2*int(sector)-1


def sector_range(code: int) -> tuple[int, int]:
    if code <= 0:
        return 0, 0
    size = code & -code
    first = (code >> size.bit_length())*size+1
    return first, first+size-1


def _header(spec: dict[str, Any], command: int) -> tuple[int, int]:
    code = sector_code(spec.get("sector"), spec.get("group"))
    return code & 0xFF, (command << 2) | ((code >> 8) & 0x03)


def _priority(spec: dict[str, Any], allow_protected: bool) -> int:
    key = spec.get("priority")
    if key not in PRIORITY_KEYS:
        raise SCOError(f"Priorität muss eine von {', '.join(PRIORITY_KEYS)} sein")
    value = PRIORITY_KEYS[key]
    if value in PROTECTED_PRIORITIES and not allow_protected:
        raise SCOError("Warn-, Sicherheits- und Gefahrenbefehle sind gesperrt, bis sie ausdrücklich freigegeben werden")
    return value


def encode(spec: dict[str, Any], allow_protected: bool = False) -> bytes:
    """Ein Telegramm aus einer Beschreibung erzeugen.

    Beispiele:
      {"sector": 36, "drive": "fix", "position": 4, "priority": "prioritaet"}  → 47 04 63 04 00 00
      {"sector": 42, "operation": "lang auf"}                                    → 53 14 80 00 00 00
      {"sector": 36, "lock": "beide", "active": True, "priority": "sicherheit"}  → Sperre setzen
      {"hex": "47 04 63 04 00 00"}                                               → roh
    """
    if "hex" in spec:
        data = parse_hex(spec["hex"])
        priority = data[2] >> 5
        if data[1] >> 2 in {1, 4} and priority in PROTECTED_PRIORITIES and not allow_protected:
            raise SCOError("Das Rohtelegramm enthält einen Warn-, Sicherheits- oder Gefahrenbefehl")
        return data
    if "drive" in spec:
        if spec["drive"] not in DRIVE:
            raise SCOError("Fahrbefehl muss oben, unten, fix oder keine sein")
        position = 0
        if spec["drive"] == "fix":
            position = int(spec.get("position", 0))
            if not 1 <= position <= 4:
                raise SCOError("Fixposition muss 1–4 sein")
        byte0, byte1 = _header(spec, 1)
        return bytes([byte0, byte1, (_priority(spec, allow_protected) << 5) | DRIVE[spec["drive"]], position, 0, 0])
    if "lock" in spec:
        if spec["lock"] not in LOCKS:
            raise SCOError("Sperrart muss fahrbefehl, taste oder beide sein")
        byte0, byte1 = _header(spec, 4)
        priority = _priority(spec, allow_protected)
        return bytes([byte0, byte1, (priority << 5) | LOCKS[spec["lock"]], 1 if spec.get("active") else 0, 0, 0])
    if "operation" in spec:
        if spec["operation"] not in OPERATION:
            raise SCOError(f"Bedienung muss eine von {', '.join(OPERATION)} sein")
        byte0, byte1 = _header(spec, 5)
        local = 0x80 if spec.get("local", True) else 0
        return bytes([byte0, byte1, local | OPERATION.index(spec["operation"]), 0, 0, 0])
    raise SCOError("Beschreibung braucht drive, lock, operation oder hex")


def safety_sequence(spec: dict[str, Any]) -> dict[str, list[bytes]]:
    """Sicherheit für einen Sektor: Setzen = Fahrbefehl + Sperre aktiv, Aufheben = Sperre passiv.

    spec: {"sector"|"group", "drive", "position", "lock", "priority"} mit einer geschützten Priorität.
    """
    if PRIORITY_KEYS.get(spec.get("priority")) not in PROTECTED_PRIORITIES:
        raise SCOError("Eine Sicherheitssequenz braucht Warn-, Sicherheits- oder Gefahrenpriorität")
    target = {key: spec[key] for key in ("sector", "group") if key in spec}
    drive = encode({**target, "drive": spec.get("drive", "oben"), "position": spec.get("position", 0),
                    "priority": spec["priority"]}, allow_protected=True)
    lock = {**target, "lock": spec.get("lock", "beide"), "priority": spec["priority"]}
    return {
        "set": [drive, encode({**lock, "active": True}, allow_protected=True)],
        "release": [encode({**lock, "active": False}, allow_protected=True)],
    }


def _action(command: int, data: bytes) -> str:
    if command == 1:
        function = data[2] & 0x1F
        if function == 3:
            return f"Fixposition P{data[3]}" if 1 <= data[3] <= 4 else f"Fixposition unbekannt ({data[3]})"
        return DRIVE_NAMES.get(function, f"unbekannter Fahrbefehl ({function})")
    if command == 4:
        if data[2] == 0:
            return "keine Sperre"
        return f"{LOCK_NAMES.get(data[2] & 0x03, 'unbekannte Sperre')} {'löschen' if data[3] == 0 else 'setzen'}"
    if command == 5:
        operation = data[2] & 0x7F
        name = OPERATION[operation] if operation < len(OPERATION) else f"unbekannt ({operation})"
        return f"{'lokal' if data[2] & 0x80 else 'Gruppe'}: {name}"
    if command in {16, 17, 19, 20, 22, 23, 24}:
        return f"Winkel {data[2]}–{data[3]}, Höhe {data[4]}–{data[5]}"
    return ""


def decode(data: bytes | list[int]) -> dict[str, Any]:
    raw = bytes(data)
    if len(raw) != 6:
        raise SCOError(f"SCO-Objekt braucht genau 6 Byte, erhalten: {len(raw)}")
    code = raw[0] | ((raw[1] & 0x03) << 8)
    command = raw[1] >> 2
    first, last = sector_range(code)
    priority = raw[2] >> 5 if command in {1, 4} else None
    return {
        "hex": to_hex(raw),
        "sector_code": code,
        "sector_from": first,
        "sector_to": last,
        "target": "reserviert" if code == 0 else (f"Sektor {first}" if first == last else f"Sektoren {first}–{last}"),
        "command_code": command,
        "command": COMMANDS.get(command, f"unbekannt ({command})"),
        "priority_code": priority,
        "priority": (PRIORITIES.get(priority, f"unbekannt ({priority})") if priority is not None else None),
        # Nur beim Fahrbefehl belegt; bei der Sperre ist die Lage der Priorität eine Annahme
        "priority_confirmed": command == 1 if priority is not None else None,
        "protected": priority in PROTECTED_PRIORITIES if priority is not None else False,
        "action": _action(command, raw),
        "lock_active": (raw[3] != 0) if command == 4 and raw[2] else None,
        "p1": raw[2], "p2": raw[3], "p3": raw[4], "p4": raw[5],
    }
