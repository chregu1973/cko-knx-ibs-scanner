import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from xknx.dpt import DPTArray
from xknx.telegram import Telegram, TelegramDirection, apci
from xknx.telegram.address import GroupAddress, IndividualAddress

from cko_ibs import sco
from cko_ibs.bus_connection import bus_connection
from cko_ibs.main import app
from cko_ibs.sco import (
    SCOError,
    decode,
    encode,
    parse_hex,
    safety_sequence,
    sector_code,
    sector_range,
)


def test_reference_telegram_from_forum_decodes_as_sector_36_priority_fix_p4() -> None:
    result = decode(parse_hex("47 04 63 04 00 00"))
    assert result["target"] == "Sektor 36"
    assert result["command"] == "Fahrbefehl"
    assert result["priority"] == "Prioritätsbefehl"
    assert result["action"] == "Beschattungsposition P4"
    assert result["priority_confirmed"] is True


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ({"sector": 36, "drive": "fix", "position": 4, "priority": "prioritaet"}, "47 04 63 04 00 00"),
        ({"sector": 36, "drive": "oben", "priority": "automatik"}, "47 04 21 00 00 00"),
        ({"sector": 36, "operation": "Stopp"}, "47 14 84 00 00 00"),
        ({"sector": 42, "operation": "lang auf"}, "53 14 80 00 00 00"),
        ({"sector": 42, "operation": "lang auf", "local": False}, "53 14 00 00 00 00"),
        ({"sector": 512, "drive": "unten", "priority": "grenz"}, "FF 07 02 00 00 00"),
    ],
)
def test_encoder_matches_flow_v3(spec, expected) -> None:
    assert sco.to_hex(encode(spec)) == expected


def test_sector_codes_for_single_sectors_and_groups_round_trip() -> None:
    for sector in range(1, 513):
        assert sector_range(sector_code(sector)) == (sector, sector)
    groups = 0
    for exponent in range(1, 10):
        size = 1 << exponent
        for start in range(1, 513, size):
            code = sector_code(group={"start": start, "size": size})
            assert sector_range(code) == (start, start+size-1)
            groups += 1
    assert groups == 511  # wie im Handoff: 511 Sektorgruppen


def test_every_operation_round_trips() -> None:
    for sector in (1, 36, 300, 512):
        for operation in sco.OPERATION:
            for local in (True, False):
                data = encode({"sector": sector, "operation": operation, "local": local})
                result = decode(data)
                assert result["target"] == f"Sektor {sector}"
                name = sco.OPERATION_NAMES[sco.OPERATION.index(operation)]
                assert result["action"] == f"{name} · {'mit Automatiksperre setzen' if local else 'mit Automatiksperre löschen'}"


def test_protected_priorities_are_blocked_unless_released() -> None:
    with pytest.raises(SCOError):
        encode({"sector": 5, "drive": "oben", "priority": "sicherheit"})
    with pytest.raises(SCOError):
        encode({"hex": "09 04 A1 00 00 00"})  # Sektor 5, Sicherheitsbefehl, obere Endlage
    data = encode({"sector": 5, "drive": "oben", "priority": "sicherheit"}, allow_protected=True)
    assert decode(data)["priority"] == "Sicherheitsbefehl" and decode(data)["protected"]


def test_safety_sequence_follows_the_central_controller() -> None:
    sequence = safety_sequence({"sector": 12, "drive": "oben", "lock": "beide", "priority": "sicherheit"})
    lock_drive, lock_key, drive = (decode(item) for item in sequence["set"])
    assert lock_drive["action"] == "Fahrbefehlssperre setzen" and lock_drive["lock_active"] is True
    assert lock_key["action"] == "Lokalbedienung sperren"
    assert drive["command"] == "Fahrbefehl" and drive["priority"] == "Sicherheitsbefehl"
    assert drive["action"] == "obere Endlage"
    released = [decode(item)["action"] for item in sequence["release"]]
    assert released == ["Fahrbefehlssperre löschen", "Lokalbedienung freigeben"]
    assert lock_key["priority"] is None  # die Sperre trägt keine Priorität
    assert {item["target"] for item in (lock_drive, lock_key, drive)} == {"Sektor 12"}
    with pytest.raises(SCOError):
        safety_sequence({"sector": 12, "priority": "automatik"})


def test_reproduces_emx8_telegrams_byte_for_byte() -> None:
    """Mitschnitt der Zentrale (Quelle 1.1.2, GA 10/0/10) vom 28.09.2026."""
    sequence = safety_sequence({"sector": 1, "drive": "oben", "lock": "taste", "priority": "gefahr"})
    assert [sco.to_hex(frame) for frame in sequence["set"]] == ["01 10 02 02 00 00", "01 04 C1 00 00 00"]
    assert [sco.to_hex(frame) for frame in sequence["release"]] == ["01 10 01 00 00 00", "01 10 02 00 00 00"]
    assert decode(parse_hex("01 10 02 02 00 00"))["action"] == "Lokalbedienung sperren"
    assert decode(parse_hex("01 04 C1 00 00 00"))["priority"] == "Gefahrenbefehl"
    assert decode(parse_hex("01 10 01 00 00 00"))["action"] == "Fahrbefehlssperre löschen"
    assert decode(parse_hex("01 58 00 FF 00 FF"))["action"] == "Winkel frei von 0 bis 255 · Höhe frei von 0 bis 255"
    assert decode(parse_hex("01 08 00 14 00 00"))["command"] == "Wertkorrektur"
    assert decode(parse_hex("01 2C 00 00 00 00"))["command"] == "Busüberwachung"


def test_setting_a_lock_is_protected_releasing_is_not() -> None:
    with pytest.raises(SCOError):
        encode({"sector": 1, "lock": "taste", "active": True})
    with pytest.raises(SCOError):
        encode({"hex": "01 10 02 02 00 00"})
    assert sco.to_hex(encode({"sector": 1, "lock": "taste", "active": False})) == "01 10 02 00 00 00"
    assert decode(parse_hex("01 10 02 02 00 00"))["protected"] is True
    assert decode(parse_hex("01 10 02 00 00 00"))["protected"] is False


def test_unknown_values_are_reported_not_guessed() -> None:
    result = decode(bytes([0x01, 12 << 2, 0, 0, 0, 0]))  # Command 12 ist nicht belegt
    assert result["command"].startswith("unbekannt")
    assert decode(bytes([0x01, 0x04, 0x03, 9, 0, 0]))["action"] == "Beschattungsposition unbekannt (9)"
    assert decode(bytes([0, 0x04, 0, 0, 0, 0]))["target"] == "reserviert"


@pytest.mark.parametrize("bad", ["47 04 63", "zz 04 63 04 00 00", ""])
def test_invalid_hex_is_rejected(bad) -> None:
    with pytest.raises(SCOError):
        parse_hex(bad)


@pytest.mark.parametrize(
    "spec",
    [{"sector": 0, "operation": "Stopp"}, {"sector": 513, "operation": "Stopp"},
     {"group": {"start": 2, "size": 4}, "operation": "Stopp"}, {"group": {"start": 1, "size": 3}, "operation": "Stopp"},
     {"sector": 1, "drive": "fix", "position": 5, "priority": "automatik"}, {"sector": 1, "drive": "seitwärts", "priority": "automatik"}],
)
def test_invalid_specs_are_rejected(spec) -> None:
    with pytest.raises(SCOError):
        encode(spec)


# --- API und Monitor --------------------------------------------------------------

client = TestClient(app)


def _incoming(ga: str, data: bytes, source: str = "1.1.10") -> Telegram:
    return Telegram(destination_address=GroupAddress(ga), source_address=IndividualAddress(source),
                    direction=TelegramDirection.INCOMING, payload=apci.GroupValueWrite(DPTArray(tuple(data))))


def test_api_preview_and_safety_pair() -> None:
    response = client.post("/api/sco/encode", json={"spec": {"sector": 36, "drive": "fix", "position": 4,
                                                                   "priority": "prioritaet"}})
    assert response.status_code == 200 and response.json()["hex"] == "47 04 63 04 00 00"
    blocked = client.post("/api/sco/encode", json={"spec": {"sector": 3, "drive": "oben", "priority": "sicherheit"}})
    assert blocked.status_code == 400
    pair = client.post("/api/sco/safety", json={"spec": {"sector": 3, "drive": "oben", "lock": "beide",
                                                               "priority": "sicherheit"}}).json()
    assert [item["decoded"]["command"] for item in pair["set"]] == ["Sperre setzen/löschen", "Sperre setzen/löschen", "Fahrbefehl"]
    assert [item["decoded"]["lock_active"] for item in pair["release"]] == [False, False]


def test_sending_requires_confirmation_release_and_connection() -> None:
    body = {"group_address": "8/0/0", "frames": ["47 04 63 04 00 00"]}
    assert client.post("/api/sco/send", json=body).status_code == 428
    assert client.post("/api/sco/send", json={**body, "confirmed": True}).status_code == 409  # nicht verbunden
    protected = {"group_address": "8/0/0", "frames": ["09 04 A1 00 00 00"], "confirmed": True}
    assert client.post("/api/sco/send", json=protected).status_code == 400
    assert client.post("/api/sco/send", json={**body, "confirmed": True, "frames": []}).status_code == 400


def test_monitor_decodes_sco_telegrams_and_builds_sector_overview() -> None:
    bus_connection.sco_log.clear()
    bus_connection.configure_group_addresses([
        {"address": "8/0/0", "name": "SCO Objekt Zentrale", "dpt": None},
        {"address": "1/1/1", "name": "Licht", "dpt": "1.001"},
    ])
    queue = bus_connection.subscribe()
    try:
        bus_connection._on_telegram(_incoming("8/0/0", bytes.fromhex("470463040000")))
        bus_connection._on_telegram(_incoming("8/0/0", bytes.fromhex("471484000000")))
        bus_connection._on_telegram(_incoming("1/1/1", bytes.fromhex("470463040000")))  # Standard-DPT: nicht deuten
        first = queue.get_nowait()
        assert first["sco"]["target"] == "Sektor 36"
        assert "Beschattungsposition P4" in first["value"]
    finally:
        bus_connection.unsubscribe(queue)
    assert client.get("/api/sco/log").json()["size"] == 2
    suggestions = client.get("/api/sco/addresses").json()["suggestions"]
    assert [row["address"] for row in suggestions] == ["8/0/0"]
    (row,) = client.get("/api/sco/sectors").json()["sectors"]
    assert row["target"] == "Sektor 36" and row["count"] == 2 and row["sources"] == {"1.1.10": 2}
    assert row["priorities"] == {"Prioritätsbefehl": 1}
    csv_text = client.get("/api/sco/export?format=csv").text
    assert "47 04 63 04 00 00" in csv_text and "Beschattungsposition P4" in csv_text
    document = client.get("/api/sco/export").json()
    assert document["schema"] == "cko.ibs.sco-log.v1" and len(document["entries"]) == 2
    assert client.put("/api/sco/addresses", json={"addresses": ["8/0/0", "8/0/1"]}).json()["addresses"] == ["8/0/0", "8/0/1"]
    assert client.put("/api/sco/addresses", json={"addresses": ["kein/ga"]}).status_code == 400
    client.delete("/api/sco/log")
    bus_connection.set_sco_addresses([])
    bus_connection.configure_group_addresses([])


def test_sending_uses_six_byte_group_value_write(monkeypatch) -> None:
    sent = []
    real_sleep = asyncio.sleep

    class FakeQueue:
        async def put(self, telegram):
            sent.append(telegram)

    class FakeXKNX:
        telegrams = FakeQueue()
        current_address = IndividualAddress("1.12.252")
        started = type("Started", (), {"is_set": staticmethod(lambda: True)})()

    monkeypatch.setattr(bus_connection, "xknx", FakeXKNX())
    monkeypatch.setattr("cko_ibs.bus_connection.asyncio.sleep", lambda seconds: real_sleep(0))
    frames = [bytes.fromhex(item["hex"].replace(" ", "")) for item in
              client.post("/api/sco/safety", json={"spec": {"sector": 7, "priority": "sicherheit"}}).json()["set"]]
    items = asyncio.run(bus_connection.send_sco("8/0/5", frames, "Sicherheit setzen"))
    assert [len(telegram.payload.to_knx()) for telegram in sent] == [8, 8]  # 2 Byte APCI + 6 Datenbyte
    assert tuple(sent[0].payload.value.value) == tuple(frames[0])
    assert items[0]["sco"]["action"] == "Lokalbedienung sperren"
    assert items[1]["sco"]["priority"] == "Sicherheitsbefehl"
    assert items[0]["source"] == "1.12.252" and items[0]["origin"] == "IBS-Test: Sicherheit setzen"
    bus_connection.sco_log.clear()
    bus_connection.set_sco_addresses([])


def test_export_is_saved_in_downloads_folder(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CKO_IBS_EXPORT_DIR", str(tmp_path))
    bus_connection.sco_log.clear()
    bus_connection._on_telegram(_incoming("10/0/10", bytes.fromhex("011002020000"), "1.1.2"))
    first = client.post("/api/sco/export/save", json={"format": "json"}).json()
    second = client.post("/api/sco/export/save", json={"format": "json"}).json()
    assert first["folder"] == str(tmp_path) and first["path"] != second["path"]
    assert second["filename"].endswith("-2.json")
    document = json.loads((tmp_path / first["filename"]).read_text(encoding="utf-8"))
    assert document["entries"][0]["decoded"]["action"] == "Lokalbedienung sperren"
    csv_file = client.post("/api/sco/export/save", json={"format": "csv"}).json()
    assert "01 10 02 02 00 00" in (tmp_path / csv_file["filename"]).read_text(encoding="utf-8-sig")
    assert client.post("/api/sco/export/save", json={"format": "xml"}).status_code == 400
    assert client.post("/api/sco/export/reveal", json={"path": first["path"]}).json()["opened"] is False  # kein Windows
    assert client.post("/api/sco/export/reveal", json={"path": "/etc/passwd"}).status_code == 400
    bus_connection.sco_log.clear()


# Paare aus dem Protokoll der ETS-App (GPA) vom 28.09.2026: Hex → Priorität, Bezeichnung der App
GPA_REFERENCE = [
    ("01 04 23 01 00 00", "Automatikbefehl", "Beschattungsposition P1"),
    ("01 04 A1 00 00 00", "Sicherheitsbefehl", "obere Endlage"),
    ("01 04 C1 00 00 00", "Gefahrenbefehl", "obere Endlage"),
    ("01 04 62 00 00 00", "Prioritätsbefehl", "untere Endlage"),
    ("01 04 65 00 00 00", "Prioritätsbefehl", "Wipp Auf · Wippdauer vom Aktor"),
    ("01 04 66 00 00 00", "Prioritätsbefehl", "Wipp Ab · Wippdauer vom Aktor"),
    ("01 04 67 00 00 00", "Prioritätsbefehl", "Stopp"),
    ("01 10 02 02 00 00", None, "Lokalbedienung sperren"),
    ("01 10 02 00 00 00", None, "Lokalbedienung freigeben"),
    ("01 10 01 00 00 00", None, "Fahrbefehlssperre löschen"),
    ("01 10 01 01 00 00", None, "Fahrbefehlssperre setzen"),
    ("01 08 00 14 00 00", None, "Lamellenwinkel: Korrekturfaktor 100 %"),
    ("01 2C 00 00 00 00", None, "inaktiv"),
    ("01 14 80 00 00 00", None, "Lang Auf · mit Automatiksperre setzen"),
    ("01 14 82 00 00 00", None, "Kurz Auf · mit Automatiksperre setzen"),
    ("01 14 84 00 00 00", None, "Stopp · mit Automatiksperre setzen"),
    ("01 14 04 00 00 00", None, "Stopp · mit Automatiksperre löschen"),
    ("01 14 00 00 00 00", None, "Lang Auf · mit Automatiksperre löschen"),
    ("01 04 25 00 00 00", "Automatikbefehl", "Wipp Auf · Wippdauer vom Aktor"),
    ("01 04 26 00 00 00", "Automatikbefehl", "Wipp Ab · Wippdauer vom Aktor"),
    ("01 04 27 00 00 00", "Automatikbefehl", "Stopp"),
]


@pytest.mark.parametrize(("hex_text", "priority", "action"), GPA_REFERENCE)
def test_decoding_matches_ets_app(hex_text, priority, action) -> None:
    result = decode(parse_hex(hex_text))
    assert result["priority"] == priority
    assert result["action"] == action


def test_grenzen_names_follow_ets_app() -> None:
    assert decode(parse_hex("01 40 00 FF 00 FF"))["command"] == "Grenzen Sicherheit"
    assert decode(parse_hex("01 4C 00 FF 00 FF"))["command"] == "Grenzen Sicherheit Lokalbedienung"
    assert decode(parse_hex("01 58 00 FF 00 FF"))["command"] == "Grenzen Automatik Lokalbedienung"


def test_lock_masks_and_invalid_combination() -> None:
    # P2 muss eine Teilmenge von P1 sein; 01/02 meldete die ETS-App als «Sperre: unbekannt»
    assert decode(parse_hex("01 10 01 02 00 00"))["action"].startswith("Sperre unbekannt")
    assert sco.to_hex(encode({"sector": 1, "lock": "fahrbefehl", "active": True}, allow_protected=True)) == "01 10 01 01 00 00"
    assert decode(parse_hex("01 10 01 01 00 00"))["action"] == "Fahrbefehlssperre setzen"


@pytest.mark.parametrize(("drive", "expected"), [("wipp_auf", "01 04 65 00 00 00"), ("wipp_ab", "01 04 66 00 00 00"),
                                                  ("stopp", "01 04 67 00 00 00")])
def test_new_drive_masks_reproduce_central_telegrams(drive, expected) -> None:
    assert sco.to_hex(encode({"sector": 1, "drive": drive, "priority": "prioritaet"})) == expected


def test_simple_release_matches_central_controller() -> None:
    """Freigabe der Zentrale (Mitschnitt 28.09.2026, 23:09:50): nur Sperren löschen, kein Fahrbefehl."""
    frames = sco.release_sequence({"sector": 1})
    assert [sco.to_hex(frame) for frame in frames] == ["01 10 01 00 00 00", "01 10 02 00 00 00"]
    assert [sco.to_hex(frame) for frame in sco.release_sequence({"sector": 1, "lock": "taste"})] == ["01 10 02 00 00 00"]
    assert [decode(frame)["action"] for frame in sco.release_sequence({"sector": 12, "lock": "fahrbefehl"})] == [
        "Fahrbefehlssperre löschen"]
    assert not any(decode(frame)["protected"] for frame in frames)
    with pytest.raises(SCOError):
        sco.release_sequence({"sector": 1, "lock": "alle"})


def test_release_api_and_sending_without_safety_release() -> None:
    result = client.post("/api/sco/release", json={"spec": {"sector": 1, "lock": "beide"}}).json()
    assert [item["hex"] for item in result["release"]] == ["01 10 01 00 00 00", "01 10 02 00 00 00"]
    # Ohne Verbindung 409 (nicht 400): die Freigabe scheitert nicht an der Sicherheitsprüfung
    response = client.post("/api/sco/send", json={"group_address": "10/0/10", "confirmed": True,
                                                   "frames": [item["hex"] for item in result["release"]]})
    assert response.status_code == 409


def test_confirmed_limit_command_decodes_ranges() -> None:
    # Anlage 21/0/253 und GPA-Referenz auf 10/0/10 (29.09.2026): Sektor 4, Grenzen Automatik Lokalbedienung
    result = decode(parse_hex("07 58 00 FF 00 FF"))
    assert result["target"] == "Sektor 4"
    assert result["command"] == "Grenzen Automatik Lokalbedienung"
    assert result["action"] == "Winkel frei von 0 bis 255 · Höhe frei von 0 bis 255"
    assert result["command_confirmed"] is True


def test_position_limits_and_unconfirmed_commands() -> None:
    # Befehl 23: ETS-App-Referenz 07 5C 00 01 00 00 = Sektor 4 · «Beschattungsposition frei von P0 bis P1»
    result = decode(parse_hex("05 5C 00 01 00 00"))
    assert result["target"] == "Sektor 3"
    assert result["command_code"] == 23
    assert result["command"] == "Grenzen Automatik Lokalbedienung"
    assert result["command_confirmed"] is True
    reference = decode(parse_hex("07 5C 00 01 00 00"))
    assert (reference["target"], reference["action"]) == ("Sektor 4", "Beschattungsposition frei von P0 bis P1")
    assert decode(parse_hex("01 44 00 02 00 00"))["command_confirmed"] is False  # 17 nur abgeleitet
    assert result["action"] == "Beschattungsposition frei von P0 bis P1"
    unknown = decode(parse_hex("01 60 00 01 00 00"))  # Befehl 24
    assert unknown["command_confirmed"] is False
    assert unknown["action"] == "Parameter 00 01 00 00 · Belegung unbestätigt"
    assert decode(parse_hex("05 04 03 01 00 00"))["command_confirmed"] is True


def test_send_without_connection_explains_that_no_project_is_needed() -> None:
    client = TestClient(app)
    response = client.post("/api/sco/send", json={
        "group_address": "10/0/10", "frames": ["01 04 21 00 00 00"], "label": "Test", "confirmed": True})
    assert response.status_code == 409
    assert "KNX-Verbindung" in response.json()["detail"]
    assert "ETS-Projekt ist nicht nötig" in response.json()["detail"]


def test_group_address_rows_take_object_size_from_communication_objects() -> None:
    from cko_ibs.project_reader import group_address_rows

    rows = group_address_rows(
        {"0/3/30": {"address": "0/3/30", "name": "G17 | Fensterreinigung | Sektor 1 | ea", "dpt": {"main": 1, "sub": 1},
                    "communication_object_ids": ["O-1"]},
         "10/0/10": {"address": "10/0/10", "name": "Beschattungszentrale", "dpt": None,
                     "communication_object_ids": ["O-2", "O-3"]},
         "1/1/1": {"address": "1/1/1", "name": "ohne Verknüpfung", "dpt": None, "communication_object_ids": []}},
        {"O-1": {"object_size": "1 Bit"}, "O-2": {"object_size": "6 Bytes"}, "O-3": {"object_size": "6 Bytes"}},
    )
    sizes = {row["address"]: row["size"] for row in rows}
    assert sizes == {"0/3/30": "1 Bit", "10/0/10": "6 Bytes", "1/1/1": None}


def test_sco_suggestions_only_list_six_byte_objects_when_sizes_are_known() -> None:
    bus_connection.configure_group_addresses([
        {"address": "0/3/30", "name": "G17 | Fensterreinigung | Sektor 1 | ea", "dpt": "1.001", "size": "1 Bit"},
        {"address": "0/3/2", "name": "G17 | Storen Zentrale", "dpt": None, "size": "6 Bytes"},
        {"address": "0/3/10", "name": "G17 | SCO Sektor 1", "dpt": None, "size": "6 Bytes"},
        {"address": "0/3/40", "name": "G17 | Storenreinigung | Sektor 1 | ea", "dpt": None, "size": "1 Bit"},
        {"address": "10/0/10", "name": "Griesser Zentrale SCO", "dpt": None, "size": None},
    ])
    try:
        assert [row["address"] for row in bus_connection.sco_suggestions()] == ["0/3/2", "0/3/10", "10/0/10"]
        assert bus_connection._sco_candidate("0/3/10") and not bus_connection._sco_candidate("0/3/30")
        assert [row["address"] for row in bus_connection.lock_reset_suggestions()] == []
    finally:
        bus_connection.configure_group_addresses([])


def test_sco_suggestions_fall_back_to_names_without_size_but_skip_one_bit_dpts() -> None:
    bus_connection.configure_group_addresses([
        {"address": "0/3/30", "name": "Fensterreinigung Sektor 1", "dpt": "1.001"},
        {"address": "10/0/10", "name": "Griesser SCO", "dpt": None},
    ])
    try:
        assert [row["address"] for row in bus_connection.sco_suggestions()] == ["10/0/10"]
    finally:
        bus_connection.configure_group_addresses([])
