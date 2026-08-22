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
from cko_ibs.usb_connection import discover_usb_devices

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"

app = FastAPI(title="CKO KNX IBS Scanner", version=__version__)
_shutdown_handler: Callable[[], None] | None = None


def set_shutdown_handler(handler: Callable[[], None] | None) -> None:
    """Register the launcher callback that stops the local Uvicorn server."""
    global _shutdown_handler
    _shutdown_handler = handler


async def _shutdown_after_response() -> None:
    # Keep the local page and pywebview bridge alive long enough for the
    # browser-side close command to reach the native window.
    await asyncio.sleep(2.0)
    if _shutdown_handler is not None:
        _shutdown_handler()


class ConnectionTestRequest(BaseModel):
    gateway_ip: str
    local_ip: str | None = None
    mode: str = "automatic"
    individual_address: str | None = None


class DeviceCheckRequest(BaseModel):
    address: str


class USBConnectionRequest(BaseModel):
    device_id: str | None = None
    individual_address: str | None = None


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
        return await bus_connection.connect(
            request.gateway_ip,
            request.local_ip,
            request.mode,
            request.individual_address,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Direkte KNX/IP-Verbindung fehlgeschlagen: {exc}",
        ) from exc


@app.post("/api/knx/connect-secure")
async def connect_secure(
    gateway_ip: Annotated[str, Form()],
    local_ip: Annotated[str | None, Form()] = None,
    individual_address: Annotated[str | None, Form()] = None,
    keyring_password: Annotated[str | None, Form()] = None,
    user_id: Annotated[int | None, Form()] = None,
    user_password: Annotated[str | None, Form()] = None,
    authentication_code: Annotated[str | None, Form()] = None,
    keyring: Annotated[UploadFile | None, File()] = None,
) -> dict:
    """Connect locally using an ETS keyring or explicit Secure credentials."""
    if keyring is None and (user_id is None or not user_password):
        raise HTTPException(
            status_code=400,
            detail="Bitte .knxkeys-Datei oder Benutzer-ID und Secure-Passwort angeben.",
        )
    try:
        if keyring is not None:
            filename = Path(keyring.filename or "project.knxkeys").name
            if not filename.lower().endswith(".knxkeys"):
                raise HTTPException(status_code=400, detail="Bitte eine .knxkeys-Datei auswählen.")
            payload = await keyring.read()
            if len(payload) > 20 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="Die Keyring-Datei ist größer als 20 MB.")
            with tempfile.TemporaryDirectory(prefix="cko-knx-secure-") as tmp_dir:
                keyring_path = Path(tmp_dir) / filename
                keyring_path.write_bytes(payload)
                return await bus_connection.connect_secure(
                    gateway_ip,
                    local_ip,
                    individual_address=individual_address,
                    keyring_path=str(keyring_path),
                    keyring_password=keyring_password,
                    user_id=user_id,
                )
        return await bus_connection.connect_secure(
            gateway_ip,
            local_ip,
            individual_address=individual_address,
            user_id=user_id,
            user_password=user_password,
            device_authentication_password=authentication_code,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"KNX IP Secure-Verbindung fehlgeschlagen: {exc}",
        ) from exc


@app.get("/api/knx/status")
async def connection_status() -> dict:
    return bus_connection.status()


@app.get("/api/knx/usb-devices")
async def usb_devices() -> dict:
    try:
        devices = [device.to_dict() for device in discover_usb_devices()]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"USB-Suche fehlgeschlagen: {exc}") from exc
    return {"devices": devices, "count": len(devices)}


@app.post("/api/knx/connect-usb")
async def connect_usb(request: USBConnectionRequest) -> dict:
    try:
        return await bus_connection.connect_usb(request.device_id, request.individual_address)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "KNX-USB-Verbindung fehlgeschlagen. ETS vollständig vom USB-Interface trennen. "
                f"Details: {exc}"
            ),
        ) from exc


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


@app.post("/api/knx/device-info")
async def device_info(request: DeviceCheckRequest) -> dict:
    try:
        return await bus_connection.read_device_info(request.address)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="Geräteinformation nicht beantwortet. Gerät oder Segment ist nicht erreichbar.",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Geräteinformation fehlgeschlagen: {exc}") from exc


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
