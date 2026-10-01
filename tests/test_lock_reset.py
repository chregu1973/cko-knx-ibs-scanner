import asyncio

from fastapi.testclient import TestClient
from xknx.dpt import DPTBinary
from xknx.telegram.address import IndividualAddress

from cko_ibs.bus_connection import bus_connection
from cko_ibs.main import app

client = TestClient(app)


def _fake_bus(monkeypatch):
    sent = []

    class FakeQueue:
        async def put(self, telegram):
            sent.append(telegram)

    class FakeXKNX:
        telegrams = FakeQueue()
        current_address = IndividualAddress("1.12.252")
        started = type("Started", (), {"is_set": staticmethod(lambda: True)})()

    monkeypatch.setattr(bus_connection, "xknx", FakeXKNX())
    return sent


def test_lock_reset_requires_confirmation() -> None:
    response = client.post("/api/sco/lock-reset", json={"group_address": "1/2/3", "value": True})
    assert response.status_code == 428


def test_lock_reset_rejects_invalid_group_address() -> None:
    response = client.post("/api/sco/lock-reset", json={"group_address": "abc", "value": True, "confirmed": True})
    assert response.status_code == 400
    assert "1/2/3" in response.json()["detail"]


def test_lock_reset_without_connection_explains_what_to_do() -> None:
    response = client.post("/api/sco/lock-reset", json={"group_address": "1/2/3", "value": False, "confirmed": True})
    assert response.status_code == 409
    assert "KNX-Verbindung" in response.json()["detail"]


def test_send_bit_writes_one_bit_group_value(monkeypatch) -> None:
    sent = _fake_bus(monkeypatch)
    item = asyncio.run(bus_connection.send_bit("1/2/3", True, "Sperre zurücksetzen"))
    asyncio.run(bus_connection.send_bit("1/2/3", False))
    assert [telegram.payload.value for telegram in sent] == [DPTBinary(1), DPTBinary(0)]
    assert str(sent[0].destination_address) == "1/2/3"
    assert item["value"] == "1 · True"
    assert item["origin"] == "IBS-Test: Sperre zurücksetzen"
    assert item["source"] == "1.12.252"


def test_lock_reset_endpoint_sends_when_connected(monkeypatch) -> None:
    sent = _fake_bus(monkeypatch)
    response = client.post("/api/sco/lock-reset", json={"group_address": " 1/2/3 ", "value": False, "confirmed": True})
    assert response.status_code == 200
    assert response.json()["sent"]["value"] == "0 · False"
    assert sent[0].payload.value == DPTBinary(0)


def test_lock_reset_suggestions_use_project_names() -> None:
    bus_connection.configure_group_addresses([
        {"address": "1/2/3", "name": "Raffstore Büro Automatik sperren", "dpt": "1.003"},
        {"address": "1/2/4", "name": "Raffstore Büro Handbetrieb", "dpt": None},
        {"address": "1/2/5", "name": "Automatik Sollwert", "dpt": "9.001"},
        {"address": "1/2/6", "name": "Licht Büro", "dpt": "1.001"},
        {"address": "1/2/7", "name": "Storen Automatik SCO", "dpt": None, "size": "6 Bytes"},
        {"address": "1/2/8", "name": "Fensterreinigung Freigabe", "dpt": None, "size": "1 Bit"},
    ])
    try:
        addresses = [row["address"] for row in client.get("/api/sco/lock-reset/suggestions").json()["suggestions"]]
        assert addresses == ["1/2/3", "1/2/4", "1/2/8"]
    finally:
        bus_connection.configure_group_addresses([])
