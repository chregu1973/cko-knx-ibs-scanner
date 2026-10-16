from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from xknx.telegram import Telegram, TelegramDirection
from xknx.telegram.address import GroupAddress, IndividualAddress

from cko_ibs.bus_connection import bus_connection
from cko_ibs.main import app

client = TestClient(app)
RESOURCES = Path(__file__).parent / "resources"


@pytest.fixture(autouse=True)
def _reset():
    bus_connection.clear_data_secure()
    yield
    bus_connection.clear_data_secure()
    bus_connection.configure_group_addresses([])


def _upload(name="DataSecure_only_one_interface.knxkeys", password="test"):
    with open(RESOURCES / name, "rb") as handle:
        return client.post("/api/knx/data-secure", files={"keyring": (name, handle.read())}, data={"password": password})


def _fake_bus(monkeypatch, address="1.0.1"):
    calls = {"init": [], "callbacks": []}

    class FakeCemi:
        data_secure = None

        def data_secure_init(self, keyring):
            calls["init"].append(keyring)
            self.data_secure = object() if keyring is not None else None

    class FakeQueue:
        def register_data_secure_group_key_issue_cb(self, callback):
            calls["callbacks"].append(callback)

    class FakeXKNX:
        cemi_handler = FakeCemi()
        telegram_queue = FakeQueue()
        current_address = IndividualAddress(address)
        started = type("Started", (), {"is_set": staticmethod(lambda: True)})()

    monkeypatch.setattr(bus_connection, "xknx", FakeXKNX())
    return calls


def test_load_keyring_without_connection() -> None:
    response = _upload()
    assert response.status_code == 200
    data = response.json()
    assert data["loaded"] is True and data["active"] is False
    assert data["group_address_count"] >= 2
    assert data["filename"] == "DataSecure_only_one_interface.knxkeys"
    assert data["interfaces"] and data["interfaces"][0]["type"] == "Tunnel"
    assert client.get("/api/knx/data-secure").json()["loaded"] is True
    assert client.delete("/api/knx/data-secure").json()["loaded"] is False


def test_wrong_password_and_wrong_file() -> None:
    assert _upload(password="falsch").status_code == 400
    assert "Passwort" in _upload(password="falsch").json()["detail"]
    response = client.post("/api/knx/data-secure", files={"keyring": ("projekt.knxproj", b"x")}, data={"password": "test"})
    assert response.status_code == 400


def test_keyring_activates_on_running_connection(monkeypatch) -> None:
    calls = _fake_bus(monkeypatch, "1.0.1")
    data = _upload().json()
    assert data["active"] is True
    assert calls["init"] and calls["init"][-1] is bus_connection.data_secure_keyring
    assert calls["callbacks"], "Callback für unlesbare Secure-Telegramme fehlt"
    assert client.delete("/api/knx/data-secure").json()["active"] is False
    assert calls["init"][-1] is None


def test_secure_groups_are_marked_in_monitor() -> None:
    _upload()
    secure = set(client.get("/api/monitor/groups").json()["secure_groups"])
    assert "0/0/1" in secure
    bus_connection.configure_group_addresses([{"address": "0/0/1", "name": "Secure Licht", "dpt": "1.001"},
                                              {"address": "5/5/5", "name": "Normal", "dpt": "1.001"}])
    groups = {row["address"]: row["secure"] for row in client.get("/api/monitor/groups").json()["groups"]}
    assert groups == {"0/0/1": True, "5/5/5": False}


def test_undecodable_secure_telegram_is_shown() -> None:
    queue = bus_connection.subscribe()
    try:
        bus_connection._on_secure_key_issue(Telegram(destination_address=GroupAddress("3/3/3"),
                                                     source_address=IndividualAddress("1.1.9"),
                                                     direction=TelegramDirection.INCOMING))
        item = queue.get_nowait()
    finally:
        bus_connection.unsubscribe(queue)
    assert item["secure"] is True and item["undecoded"] is True
    assert "Schlüssel fehlt" in item["value"]
    assert bus_connection.data_secure_status()["undecoded"] >= 1


def test_plain_tunnel_gets_keyring_for_data_secure(monkeypatch) -> None:
    import asyncio

    from cko_ibs import bus_connection as module

    created = []

    class FakeXKNX:
        def __init__(self, connection_config=None, telegram_received_cb=None):
            self.connection_config = connection_config
            self.started = type("Started", (), {"is_set": staticmethod(lambda: True)})()
            self.current_address = IndividualAddress("1.0.1")
            self.cemi_handler = type("Cemi", (), {"data_secure": None, "data_secure_init": lambda self, keyring: None})()
            self.telegram_queue = type("Queue", (), {"register_data_secure_group_key_issue_cb": lambda self, cb: None})()
            self.group_address_dpt = type("Dpt", (), {"set": lambda self, mapping: None})()
            created.append(self)

        async def start(self):
            pass

        async def stop(self):
            pass

    monkeypatch.setattr(module, "XKNX", FakeXKNX)
    _upload()
    asyncio.run(bus_connection.connect("192.168.1.20", mode="tcp"))
    config = created[-1].connection_config
    assert config.secure_config is not None and config.secure_config.keyring is bus_connection.data_secure_keyring
    asyncio.run(bus_connection._disconnect_unlocked())


def test_send_to_secure_group_warns_for_unknown_source(monkeypatch) -> None:
    import asyncio

    sent = []

    class FakeQueue:
        async def put(self, telegram):
            sent.append(telegram)

    for address, expect_warning in (("1.1.250", True), ("1.0.4", False)):
        calls = _fake_bus(monkeypatch, address)
        assert calls is not None
        bus_connection.xknx.telegrams = FakeQueue()
        bus_connection.xknx.group_address_dpt = type("Dpt", (), {"set": lambda self, mapping: None})()
        _upload()
        bus_connection.set_send_mode(True)
        item = asyncio.run(bus_connection.send_value("0/0/1", "1.001", True))
        assert item["secure"] is True
        assert bool(item.get("secure_warning")) is expect_warning, address
    bus_connection.set_send_mode(False)
