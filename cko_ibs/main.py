"""Local-only FastAPI application."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from cko_ibs import __version__
from cko_ibs.knx_discovery import discover_gateways
from cko_ibs.project_reader import read_project

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"

app = FastAPI(title="CKO KNX IBS Scanner", version=__version__)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__, "local_only": True}


@app.get("/api/knx/gateways")
async def gateways(local_ip: str | None = None, timeout: float = 3.0) -> dict:
    timeout = min(max(timeout, 1.0), 10.0)
    try:
        result = await discover_gateways(local_ip=local_ip, timeout=timeout)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"KNX/IP-Suche fehlgeschlagen: {exc}") from exc
    return {"gateways": [gateway.to_dict() for gateway in result], "count": len(result)}


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
    return {"project": summary, "stored_on_server": False}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
