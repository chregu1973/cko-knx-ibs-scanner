from fastapi.testclient import TestClient

from cko_ibs.bus_connection import connection_attempts
from cko_ibs.main import app, set_shutdown_handler
from cko_ibs.project_reader import _build_topology, _format_dpt

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


def test_automatic_connection_fallback_order() -> None:
    attempts = connection_attempts("automatic")
    assert [attempt[0] for attempt in attempts] == ["UDP", "UDP · NAT", "TCP"]
    assert connection_attempts("tcp")[0][0] == "TCP"
