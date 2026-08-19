"""Read-only ETS project import."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xknxproject import XKNXProj


def _name(value: Any, fallback: str) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("description") or fallback)
    return str(getattr(value, "name", None) or fallback)


def _format_dpt(dpt: Any) -> str | None:
    if not isinstance(dpt, dict) or dpt.get("main") is None:
        return None
    main = int(dpt["main"])
    sub = dpt.get("sub")
    return str(main) if sub is None else f"{main}.{int(sub):03d}"


def _build_topology(topology: dict[str, Any], devices: dict[str, Any]) -> list[dict[str, Any]]:
    """Build an area/line/device tree suited for the graphical local UI."""
    areas: list[dict[str, Any]] = []
    for area_address, area in topology.items():
        lines: list[dict[str, Any]] = []
        for line_address, line in (area.get("lines") or {}).items():
            line_devices = []
            for address in line.get("devices") or []:
                device = devices.get(address, {})
                line_devices.append(
                    {
                        "address": str(address),
                        "name": _name(device, "Unbenanntes Gerät"),
                        "status": "unchecked",
                    }
                )
            lines.append(
                {
                    "address": str(line_address),
                    "name": _name(line, f"Linie {line_address}"),
                    "medium": str(line.get("medium_type") or "Unbekannt"),
                    "devices": line_devices,
                }
            )
        areas.append(
            {
                "address": str(area_address),
                "name": _name(area, f"Bereich {area_address}"),
                "lines": lines,
            }
        )
    return areas


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

    group_address_rows = []
    for address, group_address in group_addresses.items():
        group_address_rows.append(
            {
                "address": str(group_address.get("address") or address),
                "name": _name(group_address, "Unbenannte Gruppenadresse"),
                "dpt": _format_dpt(group_address.get("dpt")),
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
        "topology": _build_topology(topology, devices),
        "group_addresses": group_address_rows,
    }
