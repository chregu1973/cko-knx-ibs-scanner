"""Local-only FastAPI application."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from xknx.exceptions import CouldNotParseAddress
from xknx.telegram.address import GroupAddress

from cko_ibs import __version__, sco, sco_import
from cko_ibs.bus_connection import bus_connection
from cko_ibs.knx_discovery import discover_gateways, network_adapters
from cko_ibs.project_reader import read_project
from cko_ibs.usb_connection import discover_usb_devices

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"

app = FastAPI(title="CKO KNX IBS Scanner", version=__version__)
_shutdown_handler: Callable[[], None] | None = None


@app.middleware("http")
async def disable_desktop_ui_cache(request: Request, call_next):
    """Prevent Edge WebView2 from showing UI files from an older install."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


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


# --- SCO-Objekt (6 Byte) ------------------------------------------------------


class SCOSpecRequest(BaseModel):
    spec: dict
    allow_protected: bool = False


class SCODecodeRequest(BaseModel):
    hex: str


class SCOSendRequest(BaseModel):
    group_address: str
    frames: list[str]
    label: str = ""
    confirmed: bool = False
    allow_protected: bool = False


class SCOAddressRequest(BaseModel):
    addresses: list[str]


def _sco_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@app.post("/api/sco/encode")
async def sco_encode(request: SCOSpecRequest) -> dict:
    try:
        data = sco.encode(request.spec, allow_protected=request.allow_protected)
    except (sco.SCOError, TypeError, ValueError) as exc:
        raise _sco_error(exc) from exc
    return {"hex": sco.to_hex(data), "decoded": sco.decode(data)}


@app.post("/api/sco/decode")
async def sco_decode(request: SCODecodeRequest) -> dict:
    try:
        return sco.decode(sco.parse_hex(request.hex))
    except sco.SCOError as exc:
        raise _sco_error(exc) from exc


@app.post("/api/sco/safety")
async def sco_safety(request: SCOSpecRequest) -> dict:
    try:
        sequence = sco.safety_sequence(request.spec)
    except (sco.SCOError, TypeError, ValueError) as exc:
        raise _sco_error(exc) from exc
    return {step: [{"hex": sco.to_hex(frame), "decoded": sco.decode(frame)} for frame in frames]
            for step, frames in sequence.items()}


@app.post("/api/sco/release")
async def sco_release(request: SCOSpecRequest) -> dict:
    """Vorschau der einfachen Freigabe (Sperren löschen wie die Zentrale)."""
    try:
        frames = sco.release_sequence(request.spec)
    except (sco.SCOError, TypeError, ValueError) as exc:
        raise _sco_error(exc) from exc
    return {"release": [{"hex": sco.to_hex(frame), "decoded": sco.decode(frame)} for frame in frames]}


@app.post("/api/sco/send")
async def sco_send(request: SCOSendRequest) -> dict:
    if not request.confirmed:
        raise HTTPException(status_code=428, detail="Senden muss in der Vorschau bestätigt werden.")
    if not 1 <= len(request.frames) <= 4:
        raise HTTPException(status_code=400, detail="Pro Auftrag 1 bis 4 Telegramme senden.")
    try:
        frames = [sco.parse_hex(frame) for frame in request.frames]
        for frame in frames:
            if sco.decode(frame)["protected"] and not request.allow_protected:
                raise sco.SCOError(
                    "Warn-, Sicherheits- oder Gefahrenbefehl: nur mit ausdrücklicher Freigabe senden.")
        sent = await bus_connection.send_sco(request.group_address, frames, request.label)
    except (sco.SCOError, ValueError) as exc:
        raise _sco_error(exc) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Senden fehlgeschlagen: {exc}") from exc
    return {"sent": sent}


class BitWriteRequest(BaseModel):
    group_address: str
    value: bool
    confirmed: bool = False
    label: str = "Sperre zurücksetzen"


@app.get("/api/sco/lock-reset/suggestions")
async def lock_reset_suggestions() -> dict:
    return {"suggestions": bus_connection.lock_reset_suggestions()}


@app.post("/api/sco/lock-reset")
async def lock_reset(request: BitWriteRequest) -> dict:
    """1-Bit-Telegramm senden, z. B. um die Automatiksperre eines Aktors nach Handbedienung zurückzusetzen."""
    if not request.confirmed:
        raise HTTPException(status_code=428, detail="Senden muss im Dialog bestätigt werden.")
    address = request.group_address.strip()
    try:
        address = str(GroupAddress(address))
    except CouldNotParseAddress as exc:
        raise HTTPException(status_code=400, detail=f"Ungültige Gruppenadresse «{address}» – Format z. B. 1/2/3.") from exc
    try:
        sent = await bus_connection.send_bit(address, request.value, request.label[:60])
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Senden fehlgeschlagen: {exc}") from exc
    return {"sent": sent}


@app.get("/api/sco/addresses")
async def sco_addresses() -> dict:
    return {"addresses": bus_connection.sco_addresses, "suggestions": bus_connection.sco_suggestions()}


@app.put("/api/sco/addresses")
async def set_sco_addresses(request: SCOAddressRequest) -> dict:
    try:
        return {"addresses": bus_connection.set_sco_addresses(request.addresses)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Ungültige Gruppenadresse: {exc}") from exc


@app.get("/api/sco/log")
async def sco_log() -> dict:
    return {"entries": list(bus_connection.sco_log), "size": len(bus_connection.sco_log)}


@app.delete("/api/sco/log")
async def clear_sco_log() -> dict:
    bus_connection.sco_log.clear()
    return {"entries": [], "size": 0}


@app.post("/api/sco/import")
async def sco_import_recording(
    recording: Annotated[UploadFile, File()],
    replace: Annotated[bool, Form()] = False,
    only_marked: Annotated[bool, Form()] = False,
) -> dict:
    """ETS-Aufzeichnung (XML oder Gruppenmonitor-CSV) lokal auswerten, nur 6-Byte-Gruppentelegramme."""
    filename = Path(recording.filename or "aufzeichnung").name
    payload = await recording.read(sco_import.MAX_IMPORT_BYTES+1)
    try:
        frames = sco_import.read_recording(filename, payload)
    except sco_import.ImportError_ as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = sco_import.sco_entries(filename, frames, bus_connection.group_names())
    entries = result["entries"]
    if only_marked:
        entries = [entry for entry in entries if entry["destination"] in bus_connection.sco_addresses]
    if replace:
        bus_connection.sco_log.clear()
    limit = bus_connection.sco_log.maxlen or len(entries)
    for entry in entries:
        bus_connection.sco_log.append(entry)
    return {
        "file": filename, "frames": result["frames"], "group_telegrams": result["group_telegrams"],
        "imported": len(entries), "truncated": max(0, len(entries)-limit),
        "addresses": sorted({entry["destination"] for entry in entries}),
        "entries": entries[-limit:], "size": len(bus_connection.sco_log),
    }


def _origin_group(origin: str) -> str:
    if origin.startswith("IBS-Test"):
        return "IBS-Test"
    return "Import" if origin.startswith("Import") else "bus"


def _sector_overview(entries: list[dict]) -> list[dict]:
    """Je GA, Sektor und Herkunft eine Zeile.

    Die physikalische Adresse ist kein Schlüssel: Tunnel (ETS, IBS) vergeben je Verbindung andere Adressen und Geräte
    können neu adressiert werden. Die Quellen werden deshalb pro Zeile mit Anzahl aufgeführt, Prioritäten mit ihren Quellen.
    """
    overview: dict[tuple, dict] = {}
    for entry in entries:
        decoded = entry["decoded"]
        origin = _origin_group(str(entry.get("origin", "")))
        source = str(entry.get("source") or "?")
        row = overview.setdefault((entry["destination"], decoded["target"], origin), {
            "group_address": entry["destination"], "group_name": entry.get("group_name"),
            "target": decoded["target"], "sector_from": decoded["sector_from"], "source": "", "sources": {},
            "origin": origin, "count": 0, "commands": {}, "priorities": {}, "priority_sources": {},
            "first_seen": entry["time"], "last_seen": entry["time"], "last_action": ""})
        row["count"] += 1
        row["group_name"] = row["group_name"] or entry.get("group_name")
        row["sources"][source] = row["sources"].get(source, 0)+1
        row["commands"][decoded["command"]] = row["commands"].get(decoded["command"], 0)+1
        if decoded["priority"]:
            row["priorities"][decoded["priority"]] = row["priorities"].get(decoded["priority"], 0)+1
            sources = row["priority_sources"].setdefault(decoded["priority"], [])
            if source not in sources:
                sources.append(source)
        row["first_seen"] = min(row["first_seen"], entry["time"])
        if entry["time"] >= row["last_seen"]:
            row["last_seen"], row["last_action"] = entry["time"], decoded["action"] or decoded["command"]
    for row in overview.values():
        row["source"] = ", ".join(row["sources"])
    order = {"bus": 0, "Import": 1, "IBS-Test": 2}
    return sorted(overview.values(), key=lambda row: (row["group_address"], row["sector_from"], order.get(row["origin"], 3)))


@app.get("/api/sco/sectors")
async def sco_sectors() -> dict:
    return {"sectors": _sector_overview(list(bus_connection.sco_log))}


def _sco_export_content(format: str) -> tuple[str, str, str]:
    """Dateiname, Inhalt und Medientyp des Mitschnitts."""
    entries = list(bus_connection.sco_log)
    stamp = entries[-1]["time"][:19].replace(":", "-") if entries else "leer"
    if format == "csv":
        columns = ["time", "direction", "origin", "source", "destination", "group_name", "hex", "target",
                   "command", "priority", "action", "lock_active", "p1", "p2", "p3", "p4"]
        lines = [";".join(columns)]
        for entry in entries:
            row = {**entry, **entry["decoded"]}
            lines.append(";".join(str(row.get(column, "") if row.get(column) is not None else "").replace(";", ",")
                                  for column in columns))
        return f"sco-mitschnitt-{stamp}.csv", "\ufeff"+"\n".join(lines)+"\n", "text/csv"
    if format != "json":
        raise HTTPException(status_code=400, detail="Format muss json oder csv sein.")
    document = {
        "schema": "cko.ibs.sco-log.v1", "scanner_version": __version__,
        "sco_addresses": bus_connection.sco_addresses,
        "note": "Belegung nach Flow v3/KNXUltimate und Mitschnitt einer Beschattungszentrale.",
        "entries": entries, "sectors": _sector_overview(entries),
    }
    return f"sco-mitschnitt-{stamp}.json", json.dumps(document, ensure_ascii=False, indent=2), "application/json"


def downloads_dir() -> Path:
    """Downloads-Ordner des angemeldeten Benutzers (auch wenn er unter Windows verschoben wurde)."""
    override = os.getenv("CKO_IBS_EXPORT_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            from uuid import UUID

            guid = (ctypes.c_byte * 16).from_buffer_copy(UUID("374DE290-123F-4565-9164-39C4925E467B").bytes_le)
            path_pointer = wintypes.LPWSTR()
            if ctypes.windll.shell32.SHGetKnownFolderPath(guid, 0, None, ctypes.byref(path_pointer)) == 0:
                folder = Path(path_pointer.value)
                ctypes.windll.ole32.CoTaskMemFree(path_pointer)
                return folder
        except (AttributeError, OSError, ValueError):
            pass
    folder = Path.home() / "Downloads"
    return folder if folder.is_dir() else Path.home()


class SCOExportRequest(BaseModel):
    format: str = "json"


class SCORevealRequest(BaseModel):
    path: str


@app.get("/api/sco/export")
async def sco_export(format: str = "json") -> Response:
    filename, content, media_type = _sco_export_content(format)
    return Response(content, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.post("/api/sco/export/save")
async def sco_export_save(request: SCOExportRequest) -> dict:
    """Mitschnitt direkt im Downloads-Ordner speichern (das Programmfenster blockiert Browser-Downloads)."""
    filename, content, _ = _sco_export_content(request.format)
    folder = downloads_dir()
    target = folder / filename
    counter = 2
    while target.exists():
        target = folder / f"{Path(filename).stem}-{counter}{Path(filename).suffix}"
        counter += 1
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Datei konnte nicht gespeichert werden: {exc}") from exc
    return {"path": str(target), "folder": str(folder), "filename": target.name}


@app.post("/api/sco/export/reveal")
def sco_export_reveal(request: SCORevealRequest) -> dict:
    """Gespeicherte Exportdatei im Windows-Explorer markieren."""
    target = Path(request.path).resolve()
    if target.parent != downloads_dir().resolve() or not target.is_file() or not target.name.startswith("sco-mitschnitt-"):
        raise HTTPException(status_code=400, detail="Nur eigene Exportdateien im Downloads-Ordner können angezeigt werden.")
    if sys.platform != "win32":
        return {"opened": False, "path": str(target)}
    subprocess.Popen(["explorer", f"/select,{target}"])
    return {"opened": True, "path": str(target)}


class ExternalLinkRequest(BaseModel):
    target: str


def external_links() -> dict[str, str]:
    """Feste Ziele, die im Standardbrowser statt im Programmfenster geöffnet werden."""
    feedback = urlencode({"tool": "KNX IBS Scanner", "version": __version__, "page": f"KNX IBS Scanner {__version__} (Windows)"})
    return {
        "feedback": f"https://feedback.toolbox.ckoeppen.ch/?{feedback}",
        "toolbox": "https://toolbox.ckoeppen.ch/",
        "download": DOWNLOAD_PAGE,
    }


# --- Update-Prüfung -------------------------------------------------------------
# Fragt nur die öffentliche Versionsliste der CKO Toolbox ab; es werden keine Projekt- oder Anlagendaten übertragen.

TOOLS_JSON_URL = "https://toolbox.ckoeppen.ch/data/tools.json"
DOWNLOAD_PAGE = "https://toolbox.ckoeppen.ch/tools/knx-inspector/#download"
TOOL_ID = "knx-inspector"


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = []
    for part in str(value).split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _fetch_latest_version(timeout: float = 4.0) -> str | None:
    request = urllib.request.Request(TOOLS_JSON_URL, headers={"User-Agent": f"CKO-KNX-IBS-Scanner/{__version__}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # feste HTTPS-Adresse der Toolbox
        tools = json.loads(response.read(512 * 1024).decode("utf-8"))
    entry = next((tool for tool in tools if isinstance(tool, dict) and tool.get("id") == TOOL_ID), None)
    return str(entry["version"]) if entry and entry.get("version") else None


@app.get("/api/update-check")
def update_check() -> dict:
    """Vergleicht die installierte Version mit der auf der Toolbox veröffentlichten."""
    try:
        latest = _fetch_latest_version()
    except (OSError, ValueError, KeyError, TypeError):
        return {"checked": False, "current": __version__, "latest": None, "update_available": False}
    available = bool(latest) and _version_tuple(latest) > _version_tuple(__version__)
    return {"checked": latest is not None, "current": __version__, "latest": latest,
            "update_available": available, "download_url": DOWNLOAD_PAGE}


@app.post("/api/open-external")
def open_external(request: ExternalLinkRequest) -> dict:
    """Feedback-Formular oder Toolbox im Standardbrowser öffnen; das Programmfenster bleibt beim Scanner."""
    url = external_links().get(request.target)
    if url is None:
        raise HTTPException(status_code=400, detail="Unbekanntes Linkziel.")
    return {"opened": bool(webbrowser.open(url)), "url": url}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
