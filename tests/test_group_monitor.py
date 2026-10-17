import asyncio

import pytest
from fastapi.testclient import TestClient
from xknx.dpt import DPTArray, DPTBinary
from xknx.telegram import Telegram, TelegramDirection, apci
from xknx.telegram.address import GroupAddress, IndividualAddress

from cko_ibs import group_monitor
from cko_ibs.bus_connection import bus_connection
from cko_ibs.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_monitor():
    bus_connection.set_send_mode(False)
    bus_connection.group_state.clear()
    bus_connection.configure_group_addresses([])
    yield
    bus_connection.set_send_mode(False)
    bus_connection.configure_group_addresses([])


def _fake_bus(monkeypatch, replies=None):
    """Bus-Attrappe: gesendete Telegramme sammeln, optional Rückmeldungen als eingehende Telegramme erzeugen."""
    sent = []

    class FakeQueue:
        async def put(self, telegram):
            sent.append(telegram)
            if replies:
                reply = replies(telegram)
                if reply is not None:
                    asyncio.get_running_loop().call_later(0.05, bus_connection._on_telegram, reply)

    class FakeGroupDpt:
        def set(self, _mapping):
            pass

    class FakeXKNX:
        telegrams = FakeQueue()
        current_address = IndividualAddress("1.12.252")
        started = type("Started", (), {"is_set": staticmethod(lambda: True)})()
        group_address_dpt = FakeGroupDpt()

    monkeypatch.setattr(bus_connection, "xknx", FakeXKNX())
    return sent


def _incoming(address, value, service=apci.GroupValueWrite, source="1.1.7"):
    return Telegram(destination_address=GroupAddress(address), source_address=IndividualAddress(source),
                    direction=TelegramDirection.INCOMING, payload=service(value))


# --- Kodieren / Dekodieren -----------------------------------------------------------

def test_encode_values_for_common_dpts() -> None:
    assert group_monitor.encode("1.001", "ein")[0] == DPTBinary(1)
    assert group_monitor.encode("1.001", False)[0] == DPTBinary(0)
    assert group_monitor.encode("5.001", "50")[0] == DPTArray((0x80,))
    assert group_monitor.encode("9.001", "21,5")[0] == DPTArray((0x0C, 0x33))
    assert group_monitor.encode("232.600", {"red": 255, "green": 0, "blue": 10})[0] == DPTArray((255, 0, 10))
    assert group_monitor.encode("3.007", {"control": 1, "step_code": 4})[0] == DPTBinary(12)


def test_encode_raw_and_errors() -> None:
    assert group_monitor.encode(None, None, "0C 1A")[0] == DPTArray((0x0C, 0x1A))
    assert group_monitor.encode(None, None, "1", raw_small=True)[0] == DPTBinary(1)
    with pytest.raises(group_monitor.MonitorError):
        group_monitor.encode(None, "1")
    with pytest.raises(group_monitor.MonitorError):
        group_monitor.encode("5.001", "abc")
    with pytest.raises(group_monitor.MonitorError):
        group_monitor.encode(None, None, "40", raw_small=True)


def test_json_values_and_description() -> None:
    assert group_monitor.decode("20.102", group_monitor.encode("20.102", "comfort")[0]) == "comfort"
    assert group_monitor.decode("232.600", DPTArray((1, 2, 3))) == {"red": 1, "green": 2, "blue": 3}
    assert group_monitor.describe("9.001") == {"main": 9, "unit": "°C", "value_type": "temperature"}
    assert group_monitor.json_value(float("nan")) == "nan"


def test_guess_dpt_like_ets() -> None:
    assert group_monitor.guess_dpt("Aussentemperatur °C | wer", DPTArray((0x05, 0xBE))) == "9.001"
    assert group_monitor.guess_dpt("Helligkeit Lux | wer", DPTArray((0x00, 0x64))) == "9.004"
    assert group_monitor.guess_dpt("Wind km/h | wer", DPTArray((0, 0))) == "9.028"
    assert group_monitor.guess_dpt("Irgendein Wert", DPTArray((0x05, 0xBE))) == "9"
    assert group_monitor.guess_dpt("Jalousie Position", DPTArray((0x80,))) == "5.001"
    assert group_monitor.guess_dpt("Zähler Bezug kWh", DPTArray((0, 0, 1, 0))) == "13.013"
    assert group_monitor.guess_dpt("Schalter", DPTBinary(1)) == "1.001"
    assert group_monitor.decode(group_monitor.guess_dpt("x", DPTArray((0x05, 0xBE))), DPTArray((0x05, 0xBE))) == 14.7


def test_one_byte_without_hint_is_percent_and_units_are_german() -> None:
    # Unbenannte 1-Byte-Adresse (z. B. Status Helligkeit 40 %) nicht als «102 counter pulses» anzeigen
    assert group_monitor.guess_dpt(None, DPTArray((0x66,))) == "5.001"
    assert group_monitor.decode("5.001", DPTArray((0x66,))) == 40
    assert group_monitor.describe("5.010")["unit"] == "Impulse"
    assert group_monitor.describe("7.001")["unit"] == "Impulse"
    assert group_monitor.describe("9.001")["unit"] == "°C"


def test_telegram_without_dpt_is_decoded_by_guess() -> None:
    bus_connection.configure_group_addresses([{"address": "0/1/204", "name": "Aussentemperatur °C | wer", "dpt": None}])
    queue = bus_connection.subscribe()
    try:
        bus_connection._on_telegram(_incoming("0/1/204", DPTArray((0x05, 0xBE))))
        item = queue.get_nowait()
    finally:
        bus_connection.unsubscribe(queue)
    assert item["value_json"] == 14.7 and item["dpt"] == "9.001" and item["dpt_guessed"] is True and item["unit"] == "°C"


def test_set_dpt_redecodes_last_value() -> None:
    bus_connection.configure_group_addresses([{"address": "0/1/202", "name": "Wert", "dpt": None}])
    bus_connection._on_telegram(_incoming("0/1/202", DPTArray((0x05, 0xBE))))
    response = client.put("/api/monitor/dpt", json={"group_address": "0/1/202", "dpt": "9.005"})
    state = response.json()["group"]["state"]
    assert state["value_json"] == 14.7 and state["unit"] == "m/s" and state["dpt"] == "9.005"


def test_values_match_with_tolerance() -> None:
    assert group_monitor.values_match(True, True)
    assert not group_monitor.values_match(True, False)
    assert group_monitor.values_match(50, 49.8, "5.001")
    assert not group_monitor.values_match(50, 40, "5.001")
    assert group_monitor.values_match(21.5, 21.5, "9.001")
    assert not group_monitor.values_match(1, None)


# --- Zuordnung Befehl ↔ Status --------------------------------------------------------

def test_status_pairs_by_name_and_middle_group() -> None:
    rows = [
        {"address": "1/1/1", "name": "Küche Decke", "dpt": "1.001"},
        {"address": "1/1/2", "name": "Küche Decke Status", "dpt": "1.001"},
        {"address": "1/2/5", "name": "Wohnen Pendel", "dpt": "1.001"},
        {"address": "1/4/5", "name": "Wohnen Pendel", "dpt": "1.001"},
        {"address": "2/0/3", "name": "Jalousie Süd Position", "dpt": "5.001"},
        {"address": "2/1/3", "name": "Jalousie Süd Rückmeldung Position", "dpt": "5.001"},
        {"address": "3/0/1", "name": "Temperatur Büro", "dpt": "9.001"},
    ]
    ranges = {"1/2": "Schalten", "1/4": "Status Schalten", "2/0": "Fahren", "2/1": "Status"}
    pairs = {pair["command"]: pair["status"] for pair in group_monitor.status_pairs(rows, ranges)}
    assert pairs == {"1/1/1": "1/1/2", "1/2/5": "1/4/5", "2/0/3": "2/1/3"}


def test_up_down_pairs_with_position_after_same_type() -> None:
    rows = [{"address": "2/0/1", "name": "Storen Süd", "dpt": "1.008"},
            {"address": "2/1/1", "name": "Storen Süd", "dpt": "5.001"},
            {"address": "2/2/1", "name": "Storen Süd", "dpt": "5.001"}]
    ranges = {"2/0": "Auf/Ab", "2/1": "Position", "2/2": "Status Position"}
    pairs = {pair["command"]: pair["status"] for pair in group_monitor.status_pairs(rows, ranges)}
    assert pairs == {"2/1/1": "2/2/1"}


def test_status_pairs_respect_data_types() -> None:
    rows = [{"address": "1/1/1", "name": "Licht Bad", "dpt": "1.001"},
            {"address": "1/1/2", "name": "Licht Bad Status", "dpt": "9.001"}]
    assert group_monitor.status_pairs(rows) == []


def test_sensors_are_never_suggested_as_commands() -> None:
    rows = [
        {"address": "12/2/15", "name": "MG 01 - UG Korridor Türe Magnetkontakt | Ein/Aus", "dpt": "1.002"},
        {"address": "12/2/11", "name": "MG 01 - UG Korridor Türe Magnetkontakt | Status Ein/Aus", "dpt": "1.002"},
        {"address": "1/1/33", "name": "Steckdose UG Weinkeller Gr.F1 | s-ea", "dpt": "1.001"},
        {"address": "1/1/34", "name": "Steckdose UG Weinkeller Gr.F1 | Status s-ea", "dpt": "1.001"},
        {"address": "3/0/1", "name": "Wohnen Temperatur", "dpt": "9.001"},
        {"address": "3/0/2", "name": "Wohnen Temperatur Status", "dpt": "9.001"},
        {"address": "3/1/1", "name": "Wohnen Sollwert", "dpt": "9.001"},
        {"address": "3/1/2", "name": "Wohnen Sollwert Status", "dpt": "9.001"},
        {"address": "6/0/3", "name": "Fenster Wohnen", "dpt": "1.019"},
        {"address": "6/0/4", "name": "Fenster Wohnen Status", "dpt": "1.019"},
    ]
    pairs = {pair["command"]: pair["status"] for pair in group_monitor.status_pairs(rows)}
    assert pairs == {"1/1/33": "1/1/34", "3/1/1": "3/1/2"}


def test_pipe_naming_scheme_pairs_light_and_dimmer() -> None:
    rows = [
        {"address": "1/0/1", "name": "EG Küche Decke | Ein/Aus", "dpt": "1.001"},
        {"address": "1/0/2", "name": "EG Küche Decke | Status Ein/Aus", "dpt": "1.001"},
        {"address": "1/0/3", "name": "EG Küche Decke | Helligkeitswert", "dpt": "5.001"},
        {"address": "1/0/4", "name": "EG Küche Decke | Status Helligkeitswert", "dpt": "5.001"},
        {"address": "21/0/1", "name": "Gebäude 1_Zentral_Aus__Licht_dim", "dpt": "1.001"},
        {"address": "21/0/3", "name": "Gebäude 1_Zentral_Aus__Licht_rm", "dpt": "1.001"},
    ]
    pairs = {pair["command"]: pair["status"] for pair in group_monitor.status_pairs(rows)}
    assert pairs == {"1/0/1": "1/0/2", "1/0/3": "1/0/4", "21/0/1": "21/0/3"}


def test_session_reset_keeps_connection(monkeypatch) -> None:
    _fake_bus(monkeypatch)
    bus_connection.configure_group_addresses([{"address": "1/1/1", "name": "Licht", "dpt": "1.001"}], {"1": "Licht"})
    bus_connection.group_state["1/1/1"] = {"value_json": True}
    bus_connection.set_send_mode(True)
    response = client.post("/api/session/reset", json={"clear_keyring": True})
    assert response.status_code == 200 and response.json()["connected"] is True
    groups = client.get("/api/monitor/groups").json()
    assert groups["groups"] == [] and groups["state"] == {} and groups["ranges"] == {}
    assert groups["send_mode"]["enabled"] is False


def test_range_names_are_flattened() -> None:
    ranges = {"1": {"name": "Licht", "group_ranges": {"1/2": {"name": "Status", "group_ranges": {}}}}}
    assert group_monitor.range_names(ranges) == {"1": "Licht", "1/2": "Status"}


# --- Senden über die API ----------------------------------------------------------------

def test_send_is_locked_until_released(monkeypatch) -> None:
    _fake_bus(monkeypatch)
    response = client.post("/api/monitor/send", json={"group_address": "1/1/1", "dpt": "1.001", "value": True})
    assert response.status_code == 423
    assert "freigeben" in response.json()["detail"]
    assert client.post("/api/monitor/send-mode", json={"enabled": True}).json()["enabled"] is True


def test_send_write_and_read(monkeypatch) -> None:
    sent = _fake_bus(monkeypatch)
    bus_connection.set_send_mode(True)
    response = client.post("/api/monitor/send", json={"group_address": "1/1/1", "dpt": "5.001", "value": 25})
    assert response.status_code == 200
    item = response.json()["sent"]
    assert item["value_json"] == 25 and item["hex"] == "40" and item["unit"] == "%"
    assert sent[0].payload == apci.GroupValueWrite(DPTArray((0x40,)))
    assert bus_connection.group_state["1/1/1"]["value_json"] == 25
    read = client.post("/api/monitor/send", json={"group_address": "1/1/1", "service": "read"})
    assert read.status_code == 200 and sent[1].payload == apci.GroupValueRead()


def test_send_with_unknown_dpt_needs_raw_value(monkeypatch) -> None:
    sent = _fake_bus(monkeypatch)
    bus_connection.set_send_mode(True)
    assert client.post("/api/monitor/send", json={"group_address": "4/0/1", "value": 1}).status_code == 400
    ok = client.post("/api/monitor/send", json={"group_address": "4/0/1", "raw_hex": "01", "raw_small": True})
    assert ok.status_code == 200 and sent[0].payload == apci.GroupValueWrite(DPTBinary(1))


def test_set_dpt_without_project() -> None:
    response = client.put("/api/monitor/dpt", json={"group_address": "7/1/1", "dpt": "9.001"})
    assert response.status_code == 200 and response.json()["group"]["info"]["unit"] == "°C"
    assert client.put("/api/monitor/dpt", json={"group_address": "7/1/1", "dpt": "999.9"}).status_code == 400


def test_incoming_telegram_is_enriched_and_remembered() -> None:
    bus_connection.configure_group_addresses([{"address": "3/0/1", "name": "Temperatur Büro", "dpt": "9.001"}])
    queue = bus_connection.subscribe()
    try:
        bus_connection._on_telegram(_incoming("3/0/1", DPTArray((0x0C, 0x33))))
        item = queue.get_nowait()
    finally:
        bus_connection.unsubscribe(queue)
    assert item["hex"] == "0C 33"
    assert item["group_name"] == "Temperatur Büro"
    assert item["service"] == "GroupValueWrite"
    assert bus_connection.group_state["3/0/1"]["source"] == "1.1.7"


# --- Funktionstest -------------------------------------------------------------------------

def test_function_test_ok_mismatch_and_no_reply(monkeypatch) -> None:
    bus_connection.configure_group_addresses([
        {"address": "1/1/1", "name": "Licht", "dpt": "1.001"}, {"address": "1/1/2", "name": "Licht Status", "dpt": "1.001"},
        {"address": "1/1/3", "name": "Dimmer", "dpt": "5.001"}, {"address": "1/1/4", "name": "Dimmer Status", "dpt": "5.001"}])

    def actuator(telegram):
        destination = str(telegram.destination_address)
        if destination == "1/1/1" and isinstance(telegram.payload, apci.GroupValueWrite):
            return _incoming("1/1/2", telegram.payload.value)
        if destination == "1/1/3" and isinstance(telegram.payload, apci.GroupValueWrite):
            return _incoming("1/1/4", DPTArray((0x10,)))  # Aktor meldet einen anderen Wert
        return None

    _fake_bus(monkeypatch, actuator)
    bus_connection.set_send_mode(True)
    ok = client.post("/api/monitor/function-test", json={"command": "1/1/1", "status": "1/1/2", "value": True}).json()
    assert ok["result"] == "ok" and ok["received"] is True and ok["latency_ms"] is not None
    mismatch = client.post("/api/monitor/function-test", json={"command": "1/1/3", "status": "1/1/4", "value": 80, "timeout": 1}).json()
    assert mismatch["result"] == "mismatch"
    silent = client.post("/api/monitor/function-test",
                         json={"command": "5/5/5", "status": "5/5/6", "value": True, "dpt": "1.001", "timeout": 0.5}).json()
    assert silent["result"] == "no_reply" and silent["read_used"] is True


def test_function_test_waits_for_final_blind_position(monkeypatch) -> None:
    bus_connection.configure_group_addresses([{"address": "2/1/1", "name": "Storen", "dpt": "5.001"},
                                              {"address": "2/2/1", "name": "Storen Status", "dpt": "5.001"}])

    def blind(telegram):
        if str(telegram.destination_address) == "2/1/1" and isinstance(telegram.payload, apci.GroupValueWrite):
            loop = asyncio.get_running_loop()
            for delay, raw in ((0.1, 0x33), (0.2, 0x66)):  # Zwischenpositionen 20 % und 40 %
                loop.call_later(delay, bus_connection._on_telegram, _incoming("2/2/1", DPTArray((raw,))))
            return _incoming("2/2/1", DPTArray((0x80,)))  # Endposition 50 % (nach 50 ms)
        return None

    def reorder(telegram):  # Endwert zuletzt senden
        reply = blind(telegram)
        if reply is not None:
            asyncio.get_running_loop().call_later(0.3, bus_connection._on_telegram, reply)

    _fake_bus(monkeypatch, reorder)
    bus_connection.set_send_mode(True)
    result = client.post("/api/monitor/function-test", json={"command": "2/1/1", "status": "2/2/1", "value": 50, "timeout": 2}).json()
    assert result["result"] == "ok" and result["received"] == 50


def test_function_test_requires_send_mode(monkeypatch) -> None:
    _fake_bus(monkeypatch)
    response = client.post("/api/monitor/function-test", json={"command": "1/1/1", "value": True, "dpt": "1.001"})
    assert response.status_code == 423


def test_monitor_save_writes_into_download_folder(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CKO_IBS_EXPORT_DIR", str(tmp_path))
    response = client.post("/api/monitor/save", json={"kind": "recording", "content": "{\"telegrams\": []}"})
    assert response.status_code == 200
    path = response.json()["path"]
    assert path.startswith(str(tmp_path)) and path.endswith(".json")
    assert client.post("/api/monitor/save", json={"kind": "evil", "content": "x"}).status_code == 400
    assert client.post("/api/monitor/open", json={"path": "/etc/passwd"}).status_code == 400
