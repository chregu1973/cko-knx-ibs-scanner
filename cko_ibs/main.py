"""Local-only FastAPI application."""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cko_ibs import __version__
from cko_ibs.bus_connection import bus_connection
from cko_ibs.knx_discovery import discover_gateways, network_adapters
from cko_ibs.project_reader import read_project

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"

app = FastAPI(title="CKO KNX IBS Scanner", version=__version__)
_shutdown_handler: Callable[[], None] | None = None


def set_shutdown_handler(handler: Callable[[], None] | None) -> None:
    """Register the launcher callback that stops the local Uvicorn server."""
    global _shutdown_handler
    _shutdown_handler = handler


async def _shutdown_after_response() -> None:
    await asyncio.sleep(0.4)
    if _shutdown_handler is not None:
        _shutdown_handler()


class ConnectionTestRequest(BaseModel):
    gateway_ip: str
    local_ip: str | None = None


class DeviceCheckRequest(BaseModel):
    address: str


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__, "local_only": True}


@app.post("/api/application/shutdown")
async def shutdown_application() -> dict:
    """Disconnect KNX and request a graceful stop after the response was sent."""
    await bus_connection.disconnect()
    asyncio.create_task(_shutdown_after_response())
    return {
        "status": "shutting_down",
        "knx_disconnected": True,
        "message": "KNX-Verbindung getrennt. Die Anwendung wird beendet.",
    }


@app.get("/api/knx/gateways")
async def gateways(local_ip: str | None = None, timeout: float = 3.0) -> dict:
    timeout = min(max(timeout, 1.0), 10.0)
    try:
        result = await discover_gateways(local_ip=local_ip, timeout=timeout)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"KNX/IP-Suche fehlgeschlagen: {exc}") from exc
    return {"gateways": [gateway.to_dict() for gateway in result], "count": len(result)}


@app.get("/api/network/adapters")
async def adapters() -> dict:
    try:
        result = network_adapters()
    except OSError as exc:
        raise HTTPException(status_code=502, detail=f"Netzwerkadapter konnten nicht gelesen werden: {exc}") from exc
    return {"adapters": result, "count": len(result)}


@app.post("/api/knx/test-connection")
async def test_connection(request: ConnectionTestRequest) -> dict:
    try:
        return await bus_connection.connect(request.gateway_ip, request.local_ip)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Bitte gültige IPv4-Adressen eingeben.") from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Direkte KNX/IP-Verbindung fehlgeschlagen: {exc}",
        ) from exc


@app.get("/api/knx/status")
async def connection_status() -> dict:
    return bus_connection.status()


@app.post("/api/knx/disconnect")
async def disconnect() -> dict:
    return await bus_connection.disconnect()


@app.post("/api/knx/check-device")
async def check_device(request: DeviceCheckRequest) -> dict:
    try:
        online = await bus_connection.check_device(request.address)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Ungültige physikalische KNX-Adresse.") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TimeoutError:
        online = False
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Geräteprüfung fehlgeschlagen: {exc}") from exc
    return {"address": request.address, "online": online}


@app.websocket("/api/knx/monitor")
async def telegram_monitor(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = bus_connection.subscribe()
    try:
        while True:
            await websocket.send_json(await queue.get())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus_connection.unsubscribe(queue)


@app.post("/api/project/import")
async def import_project(
    project: Annotated[UploadFile, File()],
    password: Annotated[str | None, Form()] = None,
) -> dict:
    filename = Path(project.filename or "project.knxproj").name
    if not filename.lower().endswith(".knxproj"):
        raise HTTPException(status_code=400, detail="Bitte eine .knxproj-Datei auswählen.")

    payload = await project.read()
    if len(payload) > 250 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Die Projektdatei ist größer als 250 MB.")

    with tempfile.TemporaryDirectory(prefix="cko-knx-ibs-") as tmp_dir:
        project_path = Path(tmp_dir) / filename
        project_path.write_bytes(payload)
        try:
            summary = read_project(project_path, password=password)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail="ETS-Projekt konnte nicht gelesen werden. Projektpasswort und Datei prüfen.",
            ) from exc
    bus_connection.configure_group_addresses(summary.pop("group_addresses", []))
    return {"project": summary, "stored_on_server": False}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
