import asyncio

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
    assert result["action"] == "Fixposition P4"
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
                assert result["action"] == f"{'lokal' if local else 'Gruppe'}: {operation}"


def test_protected_priorities_are_blocked_unless_released() -> None:
    with pytest.raises(SCOError):
        encode({"sector": 5, "drive": "oben", "priority": "sicherheit"})
    with pytest.raises(SCOError):
        encode({"hex": "09 04 A1 00 00 00"})  # Sektor 5, Sicherheitsbefehl, obere Endlage
    data = encode({"sector": 5, "drive": "oben", "priority": "sicherheit"}, allow_protected=True)
    assert decode(data)["priority"] == "Sicherheitsbefehl" and decode(data)["protected"]


def test_safety_sequence_sets_drive_and_lock_and_releases_with_passive_lock() -> None:
    sequence = safety_sequence({"sector": 12, "drive": "oben", "lock": "beide", "priority": "sicherheit"})
    drive, lock_on = (decode(item) for item in sequence["set"])
    (lock_off,) = (decode(item) for item in sequence["release"])
    assert drive["command"] == "Fahrbefehl" and drive["action"] == "obere Endlage"
    assert drive["priority"] == "Sicherheitsbefehl"
    assert lock_on["action"] == "Fahrbefehls- und Tastensperre setzen" and lock_on["lock_active"] is True
    assert lock_off["action"] == "Fahrbefehls- und Tastensperre löschen" and lock_off["lock_active"] is False
    assert lock_on["priority_confirmed"] is False  # Lage der Priorität bei der Sperre ist noch Annahme
    assert {item["target"] for item in (drive, lock_on, lock_off)} == {"Sektor 12"}
    with pytest.raises(SCOError):
        safety_sequence({"sector": 12, "priority": "automatik"})


def test_unknown_values_are_reported_not_guessed() -> None:
    result = decode(bytes([0x01, 12 << 2, 0, 0, 0, 0]))  # Command 12 ist nicht belegt
    assert result["command"].startswith("unbekannt")
    assert decode(bytes([0x01, 0x04, 0x03, 9, 0, 0]))["action"] == "Fixposition unbekannt (9)"
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
    assert [item["decoded"]["command"] for item in pair["set"]] == ["Fahrbefehl", "Sperre setzen/löschen"]
    assert pair["release"][0]["decoded"]["lock_active"] is False


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
        assert "Fixposition P4" in first["value"]
    finally:
        bus_connection.unsubscribe(queue)
    assert client.get("/api/sco/log").json()["size"] == 2
    suggestions = client.get("/api/sco/addresses").json()["suggestions"]
    assert [row["address"] for row in suggestions] == ["8/0/0"]
    (row,) = client.get("/api/sco/sectors").json()["sectors"]
    assert row["target"] == "Sektor 36" and row["count"] == 2 and row["source"] == "1.1.10"
    assert row["priorities"] == {"Prioritätsbefehl": 1}
    csv_text = client.get("/api/sco/export?format=csv").text
    assert "47 04 63 04 00 00" in csv_text and "Fixposition P4" in csv_text
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
    assert items[1]["sco"]["action"] == "Fahrbefehls- und Tastensperre setzen"
    assert items[0]["source"] == "1.12.252" and items[0]["origin"] == "IBS-Test: Sicherheit setzen"
    bus_connection.sco_log.clear()
    bus_connection.set_sco_addresses([])
