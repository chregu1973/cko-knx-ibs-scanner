from io import StringIO
from xml.etree import ElementTree

from fastapi.testclient import TestClient

from cko_ibs.bus_connection import BusConnection, connection_attempts
from cko_ibs.main import app, set_shutdown_handler
from cko_ibs.project_reader import (
    _build_topology,
    _classify_rf_segments,
    _device_kind,
    _format_dpt,
    _parse_line_media,
    _parse_segments,
)

client = TestClient(app)


def test_health_is_local_only() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["local_only"] is True


def test_rejects_non_ets_file() -> None:
    response = client.post(
        "/api/project/import",
        files={"project": ("project.txt", b"invalid", "text/plain")},
    )
    assert response.status_code == 400


def test_lists_network_adapters(monkeypatch) -> None:
    monkeypatch.setattr(
        "cko_ibs.main.network_adapters",
        lambda: [{"name": "Ethernet", "ip_address": "192.168.1.10"}],
    )
    response = client.get("/api/network/adapters")
    assert response.status_code == 200
    assert response.json() == {
        "adapters": [{"name": "Ethernet", "ip_address": "192.168.1.10"}],
        "count": 1,
    }


def test_rejects_invalid_gateway_ip() -> None:
    response = client.post("/api/knx/test-connection", json={"gateway_ip": "kein-ip"})
    assert response.status_code == 400


def test_device_check_requires_connection() -> None:
    response = client.post("/api/knx/check-device", json={"address": "1.1.1"})
    assert response.status_code == 409


def test_formats_ets_dpt() -> None:
    assert _format_dpt({"main": 1, "sub": 1}) == "1.001"
    assert _format_dpt({"main": 9, "sub": None}) == "9"
    assert _format_dpt(None) is None


def test_shutdown_disconnects_and_requests_stop(monkeypatch) -> None:
    stopped = []

    async def fake_disconnect() -> dict:
        return {"connected": False}

    monkeypatch.setattr("cko_ibs.main.bus_connection.disconnect", fake_disconnect)
    set_shutdown_handler(lambda: stopped.append(True))
    response = client.post("/api/application/shutdown")
    assert response.status_code == 200
    assert response.json()["knx_disconnected"] is True
    set_shutdown_handler(None)


def test_builds_area_line_device_topology() -> None:
    topology = {
        "1": {
            "name": "Hauptbereich",
            "lines": {
                "1.1": {"name": "Erdgeschoss", "medium_type": "TP", "devices": ["1.1.10"]}
            },
        }
    }
    devices = {"1.1.10": {"name": "Schaltaktor"}}
    result = _build_topology(topology, devices)
    assert result[0]["address"] == "1"
    assert result[0]["lines"][0]["address"] == "1.1"
    assert result[0]["lines"][0]["devices"][0]["name"] == "Schaltaktor"


def test_classifies_ets_dummy_as_non_physical_info() -> None:
    assert _device_kind("AndorX Server - Dummy-Gerät") == "dummy"
    assert _device_kind("Dummy") == "dummy"
    assert _device_kind("Schaltaktor") == "physical"


def test_uses_full_physical_line_address_for_device_assignment() -> None:
    topology = {
        "0": {"name": "Backbone", "lines": {"0": {"name": "Backbone", "devices": []}}},
        "1": {"name": "Bereich 1", "lines": {"0": {"name": "Hauptlinie", "devices": []}}},
    }
    devices = {"1.0.249": {"name": "Dummy"}, "1.0.250": {"name": "IP-Schnittstelle"}}
    result = _build_topology(topology, devices)
    assert result[0]["lines"][0]["full_address"] == "0.0"
    assert result[0]["lines"][0]["devices"] == []
    assert [device["address"] for device in result[1]["lines"][0]["devices"]] == [
        "1.0.249",
        "1.0.250",
    ]


def test_reads_ets6_rf_segments_and_classifies_rf_plus() -> None:
    xml = """<KNX><Project><Installations><Installation><Topology>
      <Area Address="1"><Line Address="1">
        <Segment Id="S1" Name="RF Segment Büro" MediumTypeRefId="MT-2">
          <DeviceInstance Address="67" />
        </Segment>
      </Line></Area>
    </Topology></Installation></Installations></Project></KNX>"""
    segments = _parse_segments(ElementTree.parse(StringIO(xml)))
    assert segments["1.1"][0]["medium"] == "KNX RF (RF)"
    assert segments["1.1"][0]["devices"] == ["1.1.67"]
    policies = _classify_rf_segments(segments, {"1.1.67": {"name": "KNX RF-MSG-ST"}})
    assert segments["1.1"][0]["technology"] == "rf_plus"
    assert policies["1.1.67"] == "rf_plus"


def test_reads_complete_line_medium_from_ets_xml() -> None:
    xml = """<KNX><Project><Installations><Installation><Topology>
      <Area Address="1"><Line Address="0" MediumTypeRefId="MT-5" /></Area>
    </Topology></Installation></Installations></Project></KNX>"""
    media = _parse_line_media(ElementTree.parse(StringIO(xml)))
    assert media == {"1.0": "KNXnet/IP (IP)"}


def test_automatic_connection_fallback_order() -> None:
    attempts = connection_attempts("automatic")
    assert [attempt[0] for attempt in attempts] == ["UDP", "UDP · NAT", "TCP"]
    assert connection_attempts("tcp")[0][0] == "TCP"


def test_validates_optional_tunnel_address() -> None:
    assert BusConnection._validate_individual_address(None) is None
    assert BusConnection._validate_individual_address(" 2.1.246 ") == "2.1.246"


def test_rejects_reserved_tunnel_addresses() -> None:
    for address in ("0.0.0", "15.15.255"):
        try:
            BusConnection._validate_individual_address(address)
        except ValueError as exc:
            assert "keine geeigneten Tunneladressen" in str(exc)
        else:
            raise AssertionError(f"{address} muss abgelehnt werden")


def test_secure_connection_requires_credentials() -> None:
    response = client.post(
        "/api/knx/connect-secure",
        data={"gateway_ip": "192.168.1.20"},
    )
    assert response.status_code == 400
