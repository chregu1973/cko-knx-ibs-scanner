"""Read-only ETS project import."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from xknxproject import XKNXProj
from xknxproject.models import MEDIUM_TYPES
from xknxproject.zip.extractor import extract


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


def _device_kind(name: str) -> str:
    """Classify non-physical ETS placeholders that must not be bus-tested."""
    return "dummy" if "dummy" in name.casefold() else "physical"


def _full_line_address(area_address: str, line_address: str) -> str:
    """Return an unambiguous KNX line address (eg. area 1 + line 0 -> 1.0)."""
    return line_address if "." in line_address else f"{area_address}.{line_address}"


def _medium_name(reference: str | None) -> str:
    if not reference:
        return "Unbekannt"
    suffix = reference.rsplit("_", maxsplit=1)[-1]
    return MEDIUM_TYPES.get(suffix, reference)


def _parse_segments(tree: ElementTree.ElementTree) -> dict[str, list[dict[str, Any]]]:
    """Read ETS 6 line segments which xknxproject currently flattens into a line."""
    result: dict[str, list[dict[str, Any]]] = {}
    for area in tree.findall(".//{*}Topology/{*}Area"):
        area_address = str(area.get("Address", "0"))
        for line in area.findall("{*}Line"):
            line_address = str(line.get("Address", "0"))
            full_line = _full_line_address(area_address, line_address)
            segments = []
            for index, segment in enumerate(line.findall("{*}Segment"), start=1):
                addresses = []
                for device in segment.findall(".//{*}DeviceInstance"):
                    device_address = device.get("Address")
                    if device_address is not None:
                        addresses.append(f"{full_line}.{device_address}")
                segments.append(
                    {
                        "id": str(segment.get("Id") or f"{full_line}-segment-{index}"),
                        "name": str(segment.get("Name") or f"Segment {index}"),
                        "medium": _medium_name(segment.get("MediumTypeRefId")),
                        "devices": addresses,
                    }
                )
            if segments:
                result[full_line] = segments
    return result


def _parse_line_media(tree: ElementTree.ElementTree) -> dict[str, str]:
    """Read the medium assigned to every complete line address from ETS XML."""
    result: dict[str, str] = {}
    for area in tree.findall(".//{*}Topology/{*}Area"):
        area_address = str(area.get("Address", "0"))
        for line in area.findall("{*}Line"):
            full_line = _full_line_address(area_address, str(line.get("Address", "0")))
            reference = line.get("MediumTypeRefId")
            if reference:
                result[full_line] = _medium_name(reference)
    return result


def _read_topology_extensions(
    path: Path, password: str | None
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    with (
        extract(path, password or None) as contents,
        contents.open_project_0() as project_file,
    ):
        tree = ElementTree.parse(project_file)
        return _parse_segments(tree), _parse_line_media(tree)


def _classify_rf_segments(
    segments: dict[str, list[dict[str, Any]]], devices: dict[str, Any]
) -> dict[str, str]:
    """Set the safe test policy for devices located in RF segments."""
    policies: dict[str, str] = {}
    for line_segments in segments.values():
        for segment in line_segments:
            if "RF" not in segment["medium"].upper():
                segment["technology"] = "standard"
                continue
            names = [_name(devices.get(address, {}), "") for address in segment["devices"]]
            is_multi = any(
                "multi" in name.casefold() or "medienkoppler" in name.casefold()
                for name in names
            )
            segment["technology"] = "rf_multi" if is_multi else "rf_plus"
            if not is_multi:
                policies.update(dict.fromkeys(segment["devices"], "rf_plus"))
    return policies


def _build_topology(
    topology: dict[str, Any],
    devices: dict[str, Any],
    segments: dict[str, list[dict[str, Any]]] | None = None,
    policies: dict[str, str] | None = None,
    line_media: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Build an area/line/device tree suited for the graphical local UI."""
    areas: list[dict[str, Any]] = []
    for area_address, area in topology.items():
        lines: list[dict[str, Any]] = []
        for line_address, line in (area.get("lines") or {}).items():
            full_line = _full_line_address(str(area_address), str(line_address))
            line_devices = []
            # The physical address is authoritative. It prevents local line keys
            # such as "0" in area 0 and area 1 from being confused in the UI.
            device_addresses = [
                str(address)
                for address in devices
                if str(address).rsplit(".", maxsplit=1)[0] == full_line
            ]
            for address in device_addresses:
                device = devices.get(address, {})
                device_name = _name(device, "Unbenanntes Gerät")
                line_devices.append(
                    {
                        "address": str(address),
                        "name": device_name,
                        "kind": _device_kind(device_name),
                        "test_policy": (policies or {}).get(str(address), "normal"),
                        "status": "unchecked",
                    }
                )
            lines.append(
                {
                    "address": str(line_address),
                    "full_address": full_line,
                    "name": _name(line, f"Linie {line_address}"),
                    "medium": (line_media or {}).get(
                        full_line, str(line.get("medium_type") or "Unbekannt")
                    ),
                    "role": "backbone" if full_line == "0.0" else "main" if full_line.endswith(".0") else "subline",
                    "segments": (segments or {}).get(full_line, []),
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

    segments, line_media = _read_topology_extensions(path, password)
    policies = _classify_rf_segments(segments, devices)
    device_rows = []
    for address, device in devices.items():
        device_name = _name(device, "Unbenanntes Gerät")
        device_rows.append(
            {
                "address": str(address),
                "name": device_name,
                "kind": _device_kind(device_name),
                "test_policy": policies.get(str(address), "normal"),
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
        "topology": _build_topology(
            topology, devices, segments, policies, line_media
        ),
        "group_addresses": group_address_rows,
    }
