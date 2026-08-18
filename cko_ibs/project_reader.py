"""Read-only ETS project import."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xknxproject import XKNXProj


def _name(value: Any, fallback: str) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("description") or fallback)
    return str(getattr(value, "name", None) or fallback)


def read_project(path: Path, password: str | None = None) -> dict[str, Any]:
    """Parse an ETS project without changing it and return a compact UI model."""
    project = XKNXProj(str(path), password=password or None).parse()
    info = project.get("info", {})
    devices = project.get("devices", {}) or {}
    group_addresses = project.get("group_addresses", {}) or {}
    topology = project.get("topology", {}) or {}
    locations = project.get("locations", {}) or {}

    device_rows = []
    for address, device in devices.items():
        device_rows.append(
            {
                "address": str(address),
                "name": _name(device, "Unbenanntes Gerät"),
                "status": "unchecked",
            }
        )

    return {
        "name": _name(info, path.stem),
        "filename": path.name,
        "device_count": len(devices),
        "group_address_count": len(group_addresses),
        "location_count": len(locations),
        "topology_node_count": len(topology),
        "devices": sorted(device_rows, key=lambda item: tuple(int(x) for x in item["address"].split("."))),
    }

