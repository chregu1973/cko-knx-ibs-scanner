from fastapi.testclient import TestClient

from cko_ibs.main import app

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
