"""KNX USB HID/cEMI transport used by the local Windows application.

The first supported reference device is the Siemens OCI702 USB interface.
KNX USB uses standard HID reports, so no ETS/Falcon process is involved.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from dataclasses import dataclass
from enum import Enum
from typing import Any

import hid
from xknx.cemi import CEMIFrame

LOGGER = logging.getLogger(__name__)

# KNX certified USB identifiers relevant to the Siemens family.  OCI702 units
# have appeared with Siemens' own USB IDs and with a Weinzierl OEM ID.
KNOWN_SIEMENS_USB_IDS = {
    (0x0908, 0x02DC): "Siemens OCI702 USB (Siemens HVAC)",
    (0x0908, 0x02DD): "Siemens KNX USB",
    (0x0E77, 0x0111): "Siemens KNX USB (OEM)",
}


@dataclass(slots=True)
class USBDeviceInfo:
    path: bytes
    vendor_id: int
    product_id: int
    manufacturer: str
    product: str
    serial_number: str

    @property
    def name(self) -> str:
        return KNOWN_SIEMENS_USB_IDS.get(
            (self.vendor_id, self.product_id),
            self.product or "KNX USB-Schnittstelle",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.path.hex(),
            "vendor_id": f"{self.vendor_id:04X}",
            "product_id": f"{self.product_id:04X}",
            "manufacturer": self.manufacturer,
            "product": self.product,
            "serial_number": self.serial_number,
            "name": self.name,
            "supported": (self.vendor_id, self.product_id) in KNOWN_SIEMENS_USB_IDS,
        }


def discover_usb_devices() -> list[USBDeviceInfo]:
    """Return connected Siemens OCI702-compatible HID interfaces."""
    devices: list[USBDeviceInfo] = []
    for row in hid.enumerate():
        pair = (int(row.get("vendor_id", 0)), int(row.get("product_id", 0)))
        if pair not in KNOWN_SIEMENS_USB_IDS:
            continue
        path = row.get("path")
        if isinstance(path, str):
            path = path.encode()
        if not path:
            continue
        devices.append(
            USBDeviceInfo(
                path=path,
                vendor_id=pair[0],
                product_id=pair[1],
                manufacturer=row.get("manufacturer_string") or "Siemens",
                product=row.get("product_string") or KNOWN_SIEMENS_USB_IDS[pair],
                serial_number=row.get("serial_number") or "",
            )
        )
    return devices


class _USBConnectionType(Enum):
    USB = "usb"


@dataclass(slots=True)
class _USBConnectionConfig:
    connection_type: _USBConnectionType = _USBConnectionType.USB
    threaded: bool = False


def _split_hid_reports(cemi: bytes) -> list[bytes]:
    """Wrap a cEMI frame in one or more KNX USB HID reports."""
    header = struct.pack(">BBHBBH", 0, 8, len(cemi), 1, 3, 0)
    body = header + cemi
    chunks = [body[:53]]
    body = body[53:]
    while body:
        chunks.append(body[:61])
        body = body[61:]
    reports: list[bytes] = []
    for index, chunk in enumerate(chunks, start=1):
        if len(chunks) == 1:
            packet_type = 0x03  # start + end
        elif index == 1:
            packet_type = 0x05  # start + partial
        elif index == len(chunks):
            packet_type = 0x06  # partial + end
        else:
            packet_type = 0x04  # partial
        reports.append(bytes((1, (index << 4) | packet_type, len(chunk))) + chunk.ljust(61, b"\0"))
    return reports


class _HIDAssembler:
    def __init__(self) -> None:
        self.expected = 0
        self.payload = bytearray()

    def feed(self, report: bytes) -> bytes | None:
        if len(report) == 65 and report[0] == 0:
            report = report[1:]
        if len(report) < 3 or report[0] != 1:
            return None
        packet_type = report[1] & 0x0F
        length = min(report[2], len(report) - 3)
        chunk = report[3 : 3 + length]
        if packet_type in (0x03, 0x05):
            if len(chunk) < 8:
                return None
            version, header_length, body_length, protocol, emi, _manufacturer = struct.unpack(
                ">BBHBBH", chunk[:8]
            )
            if version != 0 or header_length != 8 or protocol != 1 or emi != 3:
                return None
            self.expected = body_length
            self.payload = bytearray(chunk[8:])
        elif packet_type in (0x04, 0x06) and self.expected:
            self.payload.extend(chunk)
        else:
            return None
        if packet_type in (0x03, 0x06):
            result = bytes(self.payload[: self.expected])
            self.expected = 0
            self.payload.clear()
            return result
        return None


class KNXUSBInterface:
    """Adapter that plugs a KNX HID device into XKNX's cEMI handler."""

    def __init__(self, xknx: Any, device: USBDeviceInfo) -> None:
        self.xknx = xknx
        self.device = device
        self.connection_config = _USBConnectionConfig()
        self._handle: Any | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._send_lock = asyncio.Lock()
        self._assembler = _HIDAssembler()

    async def start(self) -> None:
        handle = hid.device()
        try:
            handle.open_path(self.device.path)
            handle.set_nonblocking(False)
        except Exception:
            handle.close()
            raise
        self._handle = handle
        self._reader_task = asyncio.create_task(self._read_loop(), name="knx-usb-reader")

    async def stop(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        if self._handle:
            await asyncio.to_thread(self._handle.close)
            self._handle = None

    async def send_cemi(self, cemi: CEMIFrame) -> None:
        if self._handle is None:
            raise RuntimeError("KNX-USB-Schnittstelle ist nicht verbunden.")
        async with self._send_lock:
            for report in _split_hid_reports(cemi.to_knx()):
                written = await asyncio.to_thread(self._handle.write, report)
                if written <= 0:
                    raise RuntimeError("Telegramm konnte nicht an KNX USB gesendet werden.")

    async def gateway_info(self) -> None:
        return None

    async def _read_loop(self) -> None:
        while self._handle is not None:
            try:
                data = await asyncio.to_thread(self._handle.read, 64, 250)
                if data and (cemi := self._assembler.feed(bytes(data))):
                    self.xknx.cemi_handler.handle_raw_cemi(cemi)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - hidapi raises platform-specific errors
                LOGGER.warning("KNX USB-Lesefehler: %s", exc)
                await asyncio.sleep(0.25)


def find_usb_device(device_id: str | None = None) -> USBDeviceInfo:
    devices = discover_usb_devices()
    if not devices:
        raise RuntimeError(
            "Keine Siemens OCI702 USB gefunden. Schnittstelle anschließen und ETS-Verbindung trennen."
        )
    if not device_id:
        return devices[0]
    for device in devices:
        if device.path.hex() == device_id:
            return device
    raise RuntimeError("Die ausgewählte KNX-USB-Schnittstelle ist nicht mehr verfügbar.")
