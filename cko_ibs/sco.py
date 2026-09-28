"""SCO-Objekt (6 Byte, «SunControl Object») kodieren und dekodieren.

Belegung nach Flow v3 (hbTec, 28.09.2026), KNXUltimate ``dpt60001`` und KNX-User-Forum:

* Byte 0 + Bit 0–1 von Byte 1: Sektorcode (10 Bit). Einzelner Sektor n → 2n−1, gerader Wert = Gruppe,
  das tiefste gesetzte Bit gibt die Gruppengrösse an.
* Byte 1, Bit 2–7: Command. Byte 2–5: Parameter P1–P4.
* Fahrbefehl (1): P1 Bit 5–7 Priorität, Bit 0–4 Funktion; P2 Fixposition 1–4.
* Sperre (4): P1 = Maske der betroffenen Sperren (1 Fahrbefehlssperre, 2 Lokalbedienung), keine
  Priorität; P2 = Maske der gesetzten Sperren, 0 = löschen (Mitschnitt und ETS-App, 28.09.2026).
* Sicherheit wie die Zentrale: zuerst Sperre setzen, dann Fahrbefehl mit Warn-/Sicherheits-/
  Gefahrenpriorität; Aufheben = Fahrbefehlssperre und Tastensperre einzeln löschen.
* Lokalbedienung (5): P1 Bit 0–6 Bedienart, Bit 7 = 1 Automatiksperre setzen / 0 Automatiksperre löschen
  (ETS-App, 28.09.2026; KNXUltimate deutet Bit 7 als lokal/Gruppe).

Das Objekt ist kein KNX-Standard-Datenpunkt. Unbekannte Werte werden roh gemeldet, nie geraten.
"""

from __future__ import annotations

import re
from typing import Any

COMMANDS = {
    1: "Fahrbefehl", 2: "Wertkorrektur", 3: "Automatikzustand", 4: "Sperre setzen/löschen",
    5: "Lokalbedienung", 6: "Szene setzen", 7: "Spezialbefehl", 8: "Datum", 9: "Zeit synchronisieren",
    10: "Sensorwert-Meldung", 11: "Busüberwachung",
    # 16, 19 und 22 sind mit der ETS-App bestätigt; 17, 20, 23 und 24 nach KNXUltimate
    16: "Grenzen Sicherheit", 17: "Fahrbereichsgrenzen Sicherheitsfahrbefehle",
    19: "Grenzen Sicherheit Lokalbedienung", 20: "Fahrbereichsgrenzen Sicherheitsfahrbefehle",
    22: "Grenzen Automatik Lokalbedienung", 23: "Fahrbereichsgrenzen Automatikfahrbefehle",
    24: "Fahrbereichsgrenzen Automatikfahrbefehle",
}
PRIORITIES = {0: "Grenzbefehl", 1: "Automatikbefehl", 3: "Prioritätsbefehl", 4: "Warnbefehl",
              5: "Sicherheitsbefehl", 6: "Gefahrenbefehl"}
PRIORITY_KEYS = {"grenz": 0, "automatik": 1, "prioritaet": 3, "warn": 4, "sicherheit": 5, "gefahr": 6}
# Diese Prioritäten können Storen verriegeln und brauchen eine ausdrückliche Freigabe.
PROTECTED_PRIORITIES = {4, 5, 6}
DRIVE = {"keine": 0, "oben": 1, "unten": 2, "fix": 3, "wipp_auf": 5, "wipp_ab": 6, "stopp": 7}
DRIVE_NAMES = {0: "keine Fahrbewegung", 1: "obere Endlage", 2: "untere Endlage", 3: "Beschattungsposition",
               5: "Wipp Auf · Wippdauer vom Aktor", 6: "Wipp Ab · Wippdauer vom Aktor", 7: "Stopp"}
OPERATION = ["lang auf", "lang ab", "kurz auf", "kurz ab", "Stopp", "lang-kurz auf", "lang-kurz ab"]
OPERATION_NAMES = ["Lang Auf", "Lang Ab", "Kurz Auf", "Kurz Ab", "Stopp", "Lang-Kurz Auf", "Lang-Kurz Ab"]
# Sperre: P1 = Maske der betroffenen Sperren, P2 = Maske der zu setzenden Sperren (0 = löschen).
# Bestätigt: 02/02 Lokalbedienung sperren, 02/00 freigeben, 01/00 Fahrbefehlssperre löschen.
# 01/02 meldet die ETS-App als «Sperre: unbekannt»; 01/01 = Fahrbefehlssperre setzen (bestätigt 28.09.2026).
LOCKS = {"fahrbefehl": 1, "taste": 2, "beide": 3}
LOCK_BITS = {1: "Fahrbefehlssperre", 2: "Lokalbedienung"}
LOCK_ACTIONS = {(1, True): "Fahrbefehlssperre setzen", (1, False): "Fahrbefehlssperre löschen",
                (2, True): "Lokalbedienung sperren", (2, False): "Lokalbedienung freigeben"}
# Sperrarten, die einzeln gesendet werden; «beide» (3) verwendet die Zentrale nicht
LOCK_PARTS = {"fahrbefehl": ["fahrbefehl"], "taste": ["taste"], "beide": ["fahrbefehl", "taste"]}


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
      {"sector": 1, "lock": "taste", "active": True}                              → 01 10 02 02 00 00
      {"hex": "47 04 63 04 00 00"}                                               → roh
    """
    if "hex" in spec:
        data = parse_hex(spec["hex"])
        if _is_protected(data) and not allow_protected:
            raise SCOError("Das Rohtelegramm enthält einen Sicherheitsbefehl oder setzt eine Sperre")
        return data
    if "drive" in spec:
        if spec["drive"] not in DRIVE:
            raise SCOError("Fahrbefehl muss oben, unten, fix, wipp_auf, wipp_ab, stopp oder keine sein")
        position = 0
        if spec["drive"] == "fix":
            position = int(spec.get("position", 0))
            if not 1 <= position <= 4:
                raise SCOError("Fixposition muss 1–4 sein")
        byte0, byte1 = _header(spec, 1)
        return bytes([byte0, byte1, (_priority(spec, allow_protected) << 5) | DRIVE[spec["drive"]], position, 0, 0])
    if "lock" in spec:
        if spec["lock"] not in {"fahrbefehl", "taste"}:
            raise SCOError("Sperrart muss fahrbefehl oder taste sein")
        if spec.get("active") and not allow_protected:
            raise SCOError("Eine Sperre setzen ist ein Sicherheitsbefehl und braucht eine ausdrückliche Freigabe")
        byte0, byte1 = _header(spec, 4)
        mask = LOCKS[spec["lock"]]
        return bytes([byte0, byte1, mask, mask if spec.get("active") else 0, 0, 0])
    if "operation" in spec:
        if spec["operation"] not in OPERATION:
            raise SCOError(f"Bedienung muss eine von {', '.join(OPERATION)} sein")
        byte0, byte1 = _header(spec, 5)
        # «local» ist der frühere Name des Schalters und bleibt als Alias erhalten
        auto_lock = spec.get("automatic_lock", spec.get("local", True))
        return bytes([byte0, byte1, (0x80 if auto_lock else 0) | OPERATION.index(spec["operation"]), 0, 0, 0])
    raise SCOError("Beschreibung braucht drive, lock, operation oder hex")


def safety_sequence(spec: dict[str, Any]) -> dict[str, list[bytes]]:
    """Sicherheit für einen Sektor, Ablauf wie bei der Zentrale.

    Setzen:   Sperre(n) setzen, danach Fahrbefehl mit Warn-, Sicherheits- oder Gefahrenpriorität.
    Aufheben: Fahrbefehlssperre und Tastensperre einzeln löschen.
    spec: {"sector"|"group", "priority", "drive", "position", "lock": "taste"|"fahrbefehl"|"beide"}
    """
    if PRIORITY_KEYS.get(spec.get("priority")) not in PROTECTED_PRIORITIES:
        raise SCOError("Eine Sicherheitssequenz braucht Warn-, Sicherheits- oder Gefahrenpriorität")
    lock_kind = spec.get("lock", "taste")
    if lock_kind not in LOCK_PARTS:
        raise SCOError("Sperrart muss taste, fahrbefehl oder beide sein")
    target = {key: spec[key] for key in ("sector", "group") if key in spec}
    locks = [encode({**target, "lock": part, "active": True}, allow_protected=True) for part in LOCK_PARTS[lock_kind]]
    drive = encode({**target, "drive": spec.get("drive", "oben"), "position": spec.get("position", 0),
                    "priority": spec["priority"]}, allow_protected=True)
    release = [encode({**target, "lock": part, "active": False}) for part in ("fahrbefehl", "taste")]
    return {"set": [*locks, drive], "release": release}


def _is_protected(data: bytes) -> bool:
    command = data[1] >> 2
    if command == 1:
        return data[2] >> 5 in PROTECTED_PRIORITIES
    if command == 4:
        return bool(data[2] & data[3])  # mindestens eine Sperre wird gesetzt
    return False


def _lock_action(data: bytes) -> str:
    affected, active = data[2], data[3]
    if affected == 0:
        return "keine Sperre"
    if affected & ~0x03 or active & ~affected:
        return f"Sperre unbekannt (P1 {affected:02X}, P2 {active:02X})"
    return " + ".join(LOCK_ACTIONS[(bit, bool(active & bit))] for bit in (1, 2) if affected & bit)


def _action(command: int, data: bytes) -> str:
    if command == 1:
        function = data[2] & 0x1F
        if function == 3:
            return f"Beschattungsposition P{data[3]}" if 1 <= data[3] <= 4 else f"Beschattungsposition unbekannt ({data[3]})"
        return DRIVE_NAMES.get(function, f"unbekannter Fahrbefehl ({function})")
    if command == 2:
        if data[2] == 0:
            return f"Lamellenwinkel: Korrekturfaktor {data[3]*5} %"  # 20 → 100 % (ETS-App)
        return f"Wertkorrektur {data[2]}: Wert {data[3]}"
    if command == 4:
        return _lock_action(data)
    if command == 5:
        operation = data[2] & 0x7F
        name = OPERATION_NAMES[operation] if operation < len(OPERATION_NAMES) else f"unbekannt ({operation})"
        return f"{name} · {'mit Automatiksperre setzen' if data[2] & 0x80 else 'mit Automatiksperre löschen'}"
    if command == 11:
        return "inaktiv" if not any(data[2:]) else f"Werte {data[2]:02X} {data[3]:02X} {data[4]:02X} {data[5]:02X}"
    if command in {16, 17, 19, 20, 22, 23, 24}:
        return f"Winkel frei von {data[2]} bis {data[3]} · Höhe frei von {data[4]} bis {data[5]}"
    return ""


def decode(data: bytes | list[int]) -> dict[str, Any]:
    raw = bytes(data)
    if len(raw) != 6:
        raise SCOError(f"SCO-Objekt braucht genau 6 Byte, erhalten: {len(raw)}")
    code = raw[0] | ((raw[1] & 0x03) << 8)
    command = raw[1] >> 2
    first, last = sector_range(code)
    priority = raw[2] >> 5 if command == 1 else None
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
        "priority_confirmed": True if priority is not None else None,
        "protected": _is_protected(raw),
        "action": _action(command, raw),
        "lock_active": bool(raw[2] & raw[3]) if command == 4 and raw[2] else None,
        "p1": raw[2], "p2": raw[3], "p3": raw[4], "p4": raw[5],
    }
