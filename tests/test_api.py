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
