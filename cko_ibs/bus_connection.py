"""Persistent local KNX/IP connection, device checks and telegram monitor."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import time
from collections import deque
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

from xknx import XKNX
from xknx.dpt import DPTArray, DPTBinary
from xknx.exceptions import XKNXException
from xknx.io import ConnectionConfig, ConnectionType, SecureConfig
from xknx.management.procedures import nm_individual_address_check
from xknx.secure.keyring import InterfaceType, Keyring, load_keyring
from xknx.telegram import Telegram, apci
from xknx.telegram.address import GroupAddress, IndividualAddress

from cko_ibs import group_monitor, sco
from cko_ibs.usb_connection import KNXUSBInterface, find_usb_device

# Erkennt SCO-Objekte an ihrer Bezeichnung im ETS-Projekt (auch an der Herstellerbezeichnung)
# Nur eindeutige Namen; «Sektor» allein steht auch in 1-Bit-Adressen (z. B. «Fensterreinigung | Sektor 1»)
SCO_NAME_HINT = re.compile(r"\bsco\b|suncontrol|griesser|6[ -]?byte", re.IGNORECASE)
LOCK_NAME_HINT = re.compile(r"sperr|automatik|hand|lokal|reset|freigabe|manuell", re.IGNORECASE)


def _is_six_bytes(size: object) -> bool:
    return bool(re.fullmatch(r"\s*6\s*bytes?\s*", str(size or ""), re.IGNORECASE))


def _is_one_bit(size: object) -> bool:
    return bool(re.fullmatch(r"\s*1\s*bits?\s*", str(size or ""), re.IGNORECASE))


def _ga_sort_key(address: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in str(address).split("/"))
    except ValueError:
        return (1 << 30,)
SCO_LOG_SIZE = 5000

DEVICE_OBJECT_PROPERTIES = {
    "object_name": 2,
    "firmware_revision": 9,
    "serial_number": 11,
    "manufacturer_id": 12,
    "program_version": 13,
    "order_info": 15,
    "pei_type": 16,
}

# Manufacturer ID 1 is defined for Siemens in the KNX manufacturer table.
# Unknown IDs deliberately remain numeric instead of being guessed.
KNX_MANUFACTURERS = {1: "Siemens"}


def connection_attempts(mode: str) -> list[tuple[str, ConnectionType, bool]]:
    """Return connection attempts in the requested order."""
    options = {
        "udp": ("UDP", ConnectionType.TUNNELING, False),
        "udp_nat": ("UDP · NAT", ConnectionType.TUNNELING, True),
        "tcp": ("TCP", ConnectionType.TUNNELING_TCP, False),
    }
    if mode == "automatic":
        return [options["udp"], options["udp_nat"], options["tcp"]]
    if mode not in options:
        raise ValueError("Unbekannte KNX/IP-Verbindungsart.")
    return [options[mode]]


class BusConnection:
    """Own the single KNX/IP tunnel used by the local application."""

    def __init__(self) -> None:
        self.xknx: XKNX | None = None
        self.gateway_ip: str | None = None
        self.local_ip: str | None = None
        self.connection_mode: str | None = None
        self.requested_individual_address: str | None = None
        self.usb_device: dict[str, Any] | None = None
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._group_addresses: dict[str, dict[str, str | None]] = {}
        self._sco_addresses: set[str] = set()
        self.sco_log: deque[dict[str, Any]] = deque(maxlen=SCO_LOG_SIZE)
        self._lock = asyncio.Lock()
        self._range_names: dict[str, str] = {}
        self.group_state: dict[str, dict[str, Any]] = {}  # letzter Wert je Gruppenadresse
        self._send_enabled_until = 0.0
        # KNX Data Secure: Schlüsselbund (.knxkeys) nur im Arbeitsspeicher, gilt für jede Verbindungsart
        self.data_secure_keyring: Keyring | None = None
        self.data_secure_info: dict[str, Any] | None = None
        self.undecoded_secure = 0
        self._guessed_dpts: dict[str, str] = {}  # vermutete Datentypen für GAs ohne DPT

    @property
    def connected(self) -> bool:
        return self.xknx is not None and self.xknx.started.is_set()

    async def connect(
        self,
        gateway_ip: str,
        local_ip: str | None = None,
        mode: str = "automatic",
        individual_address: str | None = None,
    ) -> dict[str, Any]:
        gateway = str(ipaddress.ip_address(gateway_ip))
        local = str(ipaddress.ip_address(local_ip)) if local_ip else None
        requested_address = self._validate_individual_address(individual_address)
        if requested_address and mode in {"udp", "udp_nat"}:
            raise ValueError("Eine gewünschte KNX-Tunneladresse kann nur mit TCP angefordert werden.")
        async with self._lock:
            await self._disconnect_unlocked()
            errors: list[str] = []
            attempts = connection_attempts("tcp") if requested_address and mode == "automatic" else connection_attempts(mode)
            for label, connection_type, route_back in attempts:
                config = ConnectionConfig(
                    connection_type=connection_type,
                    gateway_ip=gateway,
                    gateway_port=3671,
                    local_ip=local,
                    route_back=route_back,
                    auto_reconnect=True,
                    individual_address=requested_address,
                    secure_config=SecureConfig(keyring=self.data_secure_keyring) if self.data_secure_keyring else None,
                )
                candidate = XKNX(connection_config=config, telegram_received_cb=self._on_telegram)
                try:
                    async with asyncio.timeout(7):
                        await candidate.start()
                except (OSError, TimeoutError, XKNXException) as exc:
                    errors.append(f"{label}: {exc}")
                    with suppress(OSError, TimeoutError, XKNXException):
                        async with asyncio.timeout(2):
                            await candidate.stop()
                    continue
                self.xknx = candidate
                self.gateway_ip = gateway
                self.local_ip = local
                self.connection_mode = label
                self.requested_individual_address = requested_address
                self._apply_group_address_dpts()
                self._activate_data_secure()
                return self.status(
                    f"KNX/IP-Tunnel über {label} dauerhaft aufgebaut. Busmonitor ist aktiv."
                )
            raise RuntimeError("Kein Verbindungsmodus erfolgreich. " + " | ".join(errors))

    async def connect_usb(
        self, device_id: str | None = None, individual_address: str | None = None
    ) -> dict[str, Any]:
        """Connect directly to a Siemens OCI702 KNX USB HID interface."""
        requested_address = self._validate_individual_address(individual_address)
        if requested_address is None:
            raise ValueError(
                "Für KNX USB ist eine freie physikalische Quelladresse zwingend erforderlich. "
                "Ohne Angabe würde 0.0.0 verwendet und die Diagnose funktioniert nicht zuverlässig."
            )
        device = find_usb_device(device_id)
        async with self._lock:
            await self._disconnect_unlocked()
            candidate = XKNX(telegram_received_cb=self._on_telegram)
            candidate.knxip_interface = KNXUSBInterface(candidate, device)
            candidate.current_address = IndividualAddress(requested_address)
            try:
                async with asyncio.timeout(8):
                    await candidate.start()
            except Exception:
                with suppress(Exception):
                    async with asyncio.timeout(2):
                        await candidate.stop()
                raise
            self.xknx = candidate
            self.connection_mode = "USB · Siemens OCI702"
            self.requested_individual_address = requested_address
            self.usb_device = device.to_dict()
            self._apply_group_address_dpts()
            self._activate_data_secure()
        return self.status("Siemens OCI702 USB verbunden. Busmonitor ist aktiv.")

    async def disconnect(self) -> dict[str, Any]:
        async with self._lock:
            await self._disconnect_unlocked()
        return self.status("KNX/IP-Verbindung getrennt.")

    async def connect_secure(
        self,
        gateway_ip: str,
        local_ip: str | None = None,
        *,
        individual_address: str | None = None,
        keyring_path: str | None = None,
        keyring_password: str | None = None,
        user_id: int | None = None,
        user_password: str | None = None,
        device_authentication_password: str | None = None,
        use_loaded_keyring: bool = False,
    ) -> dict[str, Any]:
        """Establish a KNX IP Secure tunnel over TCP."""
        gateway = str(ipaddress.ip_address(gateway_ip))
        local = str(ipaddress.ip_address(local_ip)) if local_ip else None
        requested_address = self._validate_individual_address(individual_address)
        if keyring_path:
            keyring = await self._read_keyring(keyring_path, keyring_password)
            self._set_keyring(keyring, Path(keyring_path).name)
            secure = SecureConfig(keyring=keyring, user_id=user_id)
        elif use_loaded_keyring and self.data_secure_keyring is not None and not user_password:
            # Schlüsselbund aus Schritt 1 «Projekt» – muss nicht nochmals ausgewählt werden
            secure = SecureConfig(keyring=self.data_secure_keyring, user_id=user_id)
        elif user_id is not None and user_password:
            secure = SecureConfig(
                user_id=user_id,
                user_password=user_password,
                device_authentication_password=device_authentication_password or None,
                keyring=self.data_secure_keyring,  # Data Secure aus separat geladenem Schlüsselbund
            )
        else:
            raise ValueError(
                "Bitte in Schritt 1 den Schlüsselbund (.knxkeys) laden, hier eine .knxkeys-Datei wählen "
                "oder Benutzer-ID und Secure-Passwort angeben."
            )

        async with self._lock:
            await self._disconnect_unlocked()
            config = ConnectionConfig(
                connection_type=ConnectionType.TUNNELING_TCP_SECURE,
                gateway_ip=gateway,
                gateway_port=3671,
                local_ip=local,
                auto_reconnect=True,
                individual_address=requested_address,
                secure_config=secure,
            )
            candidate = XKNX(connection_config=config, telegram_received_cb=self._on_telegram)
            try:
                async with asyncio.timeout(12):
                    await candidate.start()
            except (OSError, TimeoutError, XKNXException):
                with suppress(OSError, TimeoutError, XKNXException):
                    async with asyncio.timeout(2):
                        await candidate.stop()
                raise
            self.xknx = candidate
            self.gateway_ip = gateway
            self.local_ip = local
            self.connection_mode = "TCP Secure"
            self.requested_individual_address = requested_address
            self._activate_data_secure()
            self._apply_group_address_dpts()
        return self.status(
            "Gesicherter KNX/IP-Tunnel über TCP aufgebaut. Busmonitor ist aktiv."
        )

    async def _disconnect_unlocked(self) -> None:
        if self.xknx is not None:
            await self.xknx.stop()
        self.xknx = None
        self.gateway_ip = None
        self.local_ip = None
        self.connection_mode = None
        self.requested_individual_address = None
        self.usb_device = None

    @staticmethod
    def _validate_individual_address(address: str | None) -> str | None:
        if not address or not address.strip():
            return None
        try:
            parsed = IndividualAddress(address.strip())
        except XKNXException as exc:
            raise ValueError("Ungültige physikalische KNX-Adresse.") from exc
        if str(parsed) in {"0.0.0", "15.15.255"}:
            raise ValueError(
                "0.0.0 und 15.15.255 sind keine geeigneten Tunneladressen. "
                "Bitte eine freie, am KNX/IP-Interface konfigurierte Adresse verwenden."
            )
        return str(parsed)

    async def check_device(self, address: str) -> bool:
        if not self.connected or self.xknx is None:
            raise RuntimeError("Keine aktive KNX-Verbindung.")
        try:
            async with asyncio.timeout(6):
                return await nm_individual_address_check(self.xknx, address)
        except XKNXException as exc:
            detail = str(exc).lower()
            if "l_data_con" in detail or "confirmation timed out" in detail:
                raise RuntimeError(
                    "Keine lokale Bestätigung vom KNX-Bus. Verbindung zur KNX-Linie, "
                    "Busspannung und Belegung der Schnittstelle durch ETS prüfen."
                ) from exc
            raise

    async def read_device_info(self, address: str) -> dict[str, Any]:
        """Read standardized KNX management information from one device.

        Older devices do not implement every interface-object property. Those
        fields are therefore collected independently and never make the whole
        device-information request fail.
        """
        if not self.connected or self.xknx is None:
            raise RuntimeError("Keine aktive KNX-Verbindung.")
        try:
            target = IndividualAddress(address.strip())
        except (AttributeError, XKNXException) as exc:
            raise ValueError("Ungültige physikalische KNX-Adresse.") from exc

        result: dict[str, Any] = {
            "address": str(target),
            "mask_version": None,
            "manufacturer_id": None,
            "manufacturer_name": None,
            "serial_number": None,
            "firmware_revision": None,
            "program_version": None,
            "order_info": None,
            "object_name": None,
            "pei_type": None,
            "quality": "basic",
            "quality_label": "Nur Grunddaten",
            "unavailable": [],
        }

        async with asyncio.timeout(24):
            async with self.xknx.management.connection(target) as connection:
                descriptor_telegram = await connection.request(
                    apci.DeviceDescriptorRead(descriptor=0),
                    expected=apci.DeviceDescriptorResponse,
                )
                descriptor = descriptor_telegram.payload
                if isinstance(descriptor, apci.DeviceDescriptorResponse):
                    result["mask_version"] = f"0x{descriptor.value:04X}"

                raw_properties: dict[str, bytes] = {}
                for name, property_id in DEVICE_OBJECT_PROPERTIES.items():
                    try:
                        async with asyncio.timeout(3.5):
                            telegram = await connection.request(
                                apci.PropertyValueRead(
                                    object_index=0,
                                    property_id=property_id,
                                    count=1,
                                    start_index=1,
                                ),
                                expected=apci.PropertyValueResponse,
                            )
                        payload = telegram.payload
                        if isinstance(payload, apci.PropertyValueResponse) and payload.data:
                            raw_properties[name] = payload.data
                        else:
                            result["unavailable"].append(name)
                    except (TimeoutError, XKNXException):
                        result["unavailable"].append(name)

        manufacturer = raw_properties.get("manufacturer_id")
        if manufacturer:
            manufacturer_id = int.from_bytes(manufacturer, "big")
            result["manufacturer_id"] = f"0x{manufacturer_id:04X}"
            result["manufacturer_name"] = KNX_MANUFACTURERS.get(manufacturer_id)
        serial = raw_properties.get("serial_number")
        if serial:
            result["serial_number"] = serial.hex("-").upper()
        firmware = raw_properties.get("firmware_revision")
        if firmware:
            result["firmware_revision"] = ".".join(str(byte) for byte in firmware)
        program = raw_properties.get("program_version")
        if program:
            result["program_version"] = program.hex(" ").upper()
        order = raw_properties.get("order_info")
        if order:
            result["order_info"] = self._readable_property(order)
        object_name = raw_properties.get("object_name")
        if object_name:
            result["object_name"] = self._readable_property(object_name)
        pei = raw_properties.get("pei_type")
        if pei:
            result["pei_type"] = str(int.from_bytes(pei, "big"))

        recognized_fields = sum(
            result[field] is not None
            for field in (
                "manufacturer_id",
                "serial_number",
                "firmware_revision",
                "program_version",
                "order_info",
                "object_name",
            )
        )
        if result["manufacturer_name"] and result["order_info"]:
            result["quality"] = "exact"
            result["quality_label"] = "Eindeutig erkannt"
        elif recognized_fields:
            result["quality"] = "partial"
            result["quality_label"] = "Teilweise erkannt"
        return result

    @staticmethod
    def _readable_property(value: bytes) -> str:
        text = value.rstrip(b"\x00\xff ").decode("latin-1", errors="replace").strip()
        if text and all(character.isprintable() for character in text):
            return text
        return value.hex(" ").upper()

    def status(self, message: str | None = None) -> dict[str, Any]:
        current_address = str(self.xknx.current_address) if self.xknx is not None else None
        address_warning = None
        if current_address in {"0.0.0", "15.15.255"}:
            address_warning = (
                f"Die vom Tunnel verwendete Quelladresse {current_address} ist für "
                "linienübergreifende Geräteprüfungen ungeeignet."
            )
        elif (
            self.requested_individual_address
            and current_address
            and current_address != self.requested_individual_address
        ):
            address_warning = (
                f"Angefordert war {self.requested_individual_address}, der Server hat jedoch "
                f"{current_address} zugewiesen."
            )
        return {
            "connected": self.connected,
            "gateway_ip": self.gateway_ip,
            "local_ip": self.local_ip,
            "connection_mode": self.connection_mode,
            "individual_address": current_address,
            "requested_individual_address": self.requested_individual_address,
            "usb_device": self.usb_device,
            "address_warning": address_warning,
            "data_secure": self.data_secure_status(),
            "message": message,
        }

    def configure_group_addresses(self, rows: list[dict[str, str | None]],
                                  range_names: dict[str, str] | None = None) -> None:
        """Keep ETS group address metadata in local memory for monitor enrichment."""
        self._group_addresses = {str(row["address"]): row for row in rows}
        self._range_names = dict(range_names or {})
        self._apply_group_address_dpts()

    def _apply_group_address_dpts(self) -> None:
        if self.xknx is None:
            return
        dpts = {
            address: row["dpt"]
            for address, row in self._group_addresses.items()
            if row.get("dpt")
        }
        self.xknx.group_address_dpt.set(dpts)

    # --- SCO-Objekt ---------------------------------------------------------

    def sco_suggestions(self) -> list[dict[str, str | None]]:
        """GAs aus dem ETS-Projekt mit 6-Byte-Objekten (SCO); GAs ohne verknüpftes Objekt über den Namen."""
        rows = [row for _address, row in sorted(self._group_addresses.items(), key=lambda item: _ga_sort_key(item[0]))]
        # Bekannte Objektgrösse entscheidet; ohne verknüpftes Objekt (z. B. Zentrale nicht im Projekt) zählt der Name
        return [row for row in rows if _is_six_bytes(row.get("size")) or (
            not row.get("size") and SCO_NAME_HINT.search(str(row.get("name") or ""))
            and not str(row.get("dpt") or "").startswith("1."))]

    def group_names(self) -> dict[str, str | None]:
        """GA-Bezeichnungen aus dem geladenen ETS-Projekt (für importierte Aufzeichnungen)."""
        return {address: row.get("name") for address, row in self._group_addresses.items()}

    @property
    def sco_addresses(self) -> list[str]:
        return sorted(self._sco_addresses)

    def set_sco_addresses(self, addresses: list[str]) -> list[str]:
        valid = set()
        for address in addresses:
            valid.add(str(GroupAddress(str(address).strip())))
        self._sco_addresses = valid
        return self.sco_addresses

    def _sco_candidate(self, destination: str) -> bool:
        # Markierte GAs immer; sonst 6-Byte-Objekte laut Projekt bzw. GAs ohne bekannten Standard-DPT
        if destination in self._sco_addresses:
            return True
        row = self._group_addresses.get(destination, {})
        if row.get("size"):
            return _is_six_bytes(row.get("size"))
        return not row.get("dpt")

    def _publish(self, item: dict[str, Any]) -> None:
        for queue in tuple(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(item)

    def _attach_sco(self, item: dict[str, Any], payload: Any) -> None:
        value = getattr(payload, "value", None)
        if not isinstance(value, DPTArray) or len(value.value) != 6:
            return
        if not self._sco_candidate(item["destination"]):
            return
        decoded = sco.decode(bytes(value.value))
        item["sco"] = decoded
        item["value"] = f"{decoded['target']} · {decoded['command']}" + (
            f" · {decoded['priority']}" if decoded["priority"] else "") + (
            f" · {decoded['action']}" if decoded["action"] else "")
        self.sco_log.append({key: item.get(key) for key in (
            "time", "direction", "source", "destination", "group_name", "service", "secure")} | {
            "hex": decoded["hex"], "decoded": decoded, "origin": item.get("origin", "bus")})

    def lock_reset_suggestions(self) -> list[dict[str, str | None]]:
        """1-Bit-GAs aus dem ETS-Projekt, deren Name auf eine Sperre oder Automatik/Handbetrieb hindeutet."""
        rows = [row for _address, row in sorted(self._group_addresses.items(), key=lambda item: _ga_sort_key(item[0]))]
        return [row for row in rows if LOCK_NAME_HINT.search(str(row.get("name") or ""))
                and (_is_one_bit(row.get("size")) if row.get("size")
                     else (not row.get("dpt") or str(row.get("dpt")).startswith("1.")))]

    async def send_bit(self, group_address: str, value: bool, label: str = "") -> dict[str, Any]:
        """Einzelnes 1-Bit-Telegramm (GroupValueWrite) senden, z. B. um eine Automatiksperre zurückzusetzen."""
        if not self.connected or self.xknx is None:
            raise RuntimeError("Keine aktive KNX-Verbindung. Zuerst in Schritt 2 «Verbindung» mit der KNX-Schnittstelle verbinden – ein ETS-Projekt ist nicht nötig.")
        destination = GroupAddress(group_address)
        payload = apci.GroupValueWrite(DPTBinary(1 if value else 0))
        await self.xknx.telegrams.put(Telegram(destination_address=destination, payload=payload))
        item = {
            "time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "direction": "Outgoing",
            "source": str(self.xknx.current_address),
            "destination": str(destination),
            "group_name": self._group_addresses.get(str(destination), {}).get("name"),
            "dpt": "1.x",
            "service": "GroupValueWrite",
            "value": "1 · True" if value else "0 · False",
            "raw": str(payload),
            "secure": False,
            "origin": f"IBS-Test{': ' + label if label else ''}",
        }
        self._publish(item)
        return item

    async def send_sco(self, group_address: str, frames: list[bytes], label: str = "") -> list[dict[str, Any]]:
        """6-Byte-Telegramme nacheinander als GroupValueWrite senden und protokollieren."""
        if not self.connected or self.xknx is None:
            raise RuntimeError("Keine aktive KNX-Verbindung. Zuerst in Schritt 2 «Verbindung» mit der KNX-Schnittstelle verbinden – ein ETS-Projekt ist nicht nötig.")
        destination = GroupAddress(group_address)
        sent = []
        for index, frame in enumerate(frames):
            if len(frame) != 6:
                raise ValueError("SCO-Telegramme müssen genau 6 Byte lang sein.")
            payload = apci.GroupValueWrite(DPTArray(tuple(frame)))
            await self.xknx.telegrams.put(Telegram(destination_address=destination, payload=payload))
            item = {
                "time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "direction": "Outgoing",
                "source": str(self.xknx.current_address),
                "destination": str(destination),
                "group_name": self._group_addresses.get(str(destination), {}).get("name"),
                "dpt": None,
                "service": "GroupValueWrite",
                "value": None,
                "raw": str(payload),
                "secure": False,
                "origin": f"IBS-Test{': ' + label if label else ''}",
            }
            self._sco_addresses.add(str(destination))
            self._attach_sco(item, payload)
            self._publish(item)
            sent.append(item)
            if index < len(frames)-1:
                await asyncio.sleep(0.25)  # Reihenfolge Fahrbefehl → Sperre auf dem Bus sicherstellen
        return sent

    def reset_session(self, clear_keyring: bool = True) -> dict[str, Any]:
        """Projektwechsel: alle Projekt- und Monitordaten vergessen, die KNX-Verbindung bleibt bestehen."""
        self._group_addresses = {}
        self._range_names = {}
        self.group_state.clear()
        self._guessed_dpts.clear()
        self._sco_addresses.clear()
        self.sco_log.clear()
        self.undecoded_secure = 0
        self._send_enabled_until = 0.0
        self._apply_group_address_dpts()
        if clear_keyring:
            self.clear_data_secure()
        return self.status("Zurückgesetzt – bereit für ein neues Projekt.")

    # --- KNX Data Secure ------------------------------------------------------------

    @staticmethod
    async def _read_keyring(path: str, password: str | None) -> Keyring:
        if not password:
            raise ValueError("Für die .knxkeys-Datei ist ein Passwort erforderlich.")
        try:
            return await load_keyring(path, password)
        except Exception as exc:
            raise ValueError("Schlüsselbund konnte nicht geöffnet werden – Passwort der .knxkeys-Datei prüfen.") from exc

    def _set_keyring(self, keyring: Keyring, filename: str) -> None:
        keys = keyring.get_data_secure_group_keys()
        interfaces = []
        for interface in keyring.interfaces:
            if interface.individual_address is None:
                continue
            secure_groups = [ga for ga in interface.group_addresses if ga in keys]
            interfaces.append({
                "individual_address": str(interface.individual_address),
                "type": {InterfaceType.TUNNELING: "Tunnel", InterfaceType.USB: "USB",
                         InterfaceType.BACKBONE: "Backbone"}.get(interface.type, str(interface.type)),
                "user_id": interface.user_id,
                "host": str(interface.host) if interface.host else None,
                "secure_group_count": len(secure_groups),
            })
        self.data_secure_keyring = keyring
        self.data_secure_info = {
            "filename": filename,
            "group_addresses": sorted((str(ga) for ga in keys), key=_ga_sort_key),
            "group_address_count": len(keys),
            "sender_count": len(keyring.get_data_secure_senders()),
            "interfaces": sorted(interfaces, key=lambda item: -item["secure_group_count"])[:24],
        }

    async def load_data_secure(self, path: str, password: str | None, filename: str) -> dict[str, Any]:
        """Schlüsselbund für KNX Data Secure laden; wirkt sofort auf eine bestehende Verbindung."""
        keyring = await self._read_keyring(path, password)
        self._set_keyring(keyring, filename)
        self._activate_data_secure()
        return self.data_secure_status()

    def clear_data_secure(self) -> dict[str, Any]:
        self.data_secure_keyring = None
        self.data_secure_info = None
        if self.xknx is not None and hasattr(self.xknx, "cemi_handler"):
            self.xknx.cemi_handler.data_secure_init(keyring=None)
        return self.data_secure_status()

    def _activate_data_secure(self) -> None:
        """Data Secure in der laufenden xknx-Instanz einschalten und unlesbare Secure-Telegramme melden."""
        if self.xknx is None:
            return
        if self.data_secure_keyring is not None:
            self.xknx.cemi_handler.data_secure_init(keyring=self.data_secure_keyring)
        with suppress(AttributeError):
            self.xknx.telegram_queue.register_data_secure_group_key_issue_cb(self._on_secure_key_issue)

    def data_secure_status(self) -> dict[str, Any]:
        info = self.data_secure_info or {}
        current = str(self.xknx.current_address) if self.xknx is not None else None
        interface = next((item for item in info.get("interfaces", []) if item["individual_address"] == current), None)
        active = bool(self.xknx is not None and getattr(getattr(self.xknx, "cemi_handler", None), "data_secure", None) is not None)
        return {
            "loaded": self.data_secure_keyring is not None,
            "active": active,
            "filename": info.get("filename"),
            "group_address_count": info.get("group_address_count", 0),
            "interfaces": info.get("interfaces", []),
            "current_address": current,
            "current_interface": interface,
            "can_send": bool(interface and interface["secure_group_count"]),
            "undecoded": self.undecoded_secure,
        }

    def secure_groups(self) -> set[str]:
        return set((self.data_secure_info or {}).get("group_addresses", []))

    def _on_secure_key_issue(self, telegram: Telegram) -> None:
        """Verschlüsseltes Gruppentelegramm ohne passenden Schlüssel – trotzdem im Monitor zeigen."""
        self.undecoded_secure += 1
        destination = str(telegram.destination_address)
        row = self._group_addresses.get(destination, {})
        self._publish({
            "time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "direction": telegram.direction.value,
            "source": str(telegram.source_address),
            "destination": destination,
            "group_name": row.get("name"),
            "dpt": row.get("dpt"),
            "service": "DataSecure",
            "value": "🔒 verschlüsselt – Schlüssel fehlt",
            "value_json": None,
            "unit": None,
            "hex": "",
            "small": False,
            "raw": "",
            "secure": True,
            "undecoded": True,
        })

    # --- Gruppenmonitor: Werte, Senden, Funktionstest -------------------------------

    def _remember(self, item: dict[str, Any]) -> None:
        if item.get("service") in {"GroupValueWrite", "GroupValueResponse"}:
            self.group_state[item["destination"]] = {key: item.get(key) for key in (
                "time", "source", "service", "value", "value_json", "unit", "hex", "dpt", "direction", "small",
                "dpt_guessed")}

    def group_rows(self) -> list[dict[str, Any]]:
        rows = [row for _address, row in sorted(self._group_addresses.items(), key=lambda item: _ga_sort_key(item[0]))]
        secure = self.secure_groups()
        return [dict(row) | {"info": group_monitor.describe(row.get("dpt")), "secure": str(row.get("address")) in secure}
                for row in rows]

    @property
    def range_names(self) -> dict[str, str]:
        return dict(self._range_names)

    def status_pairs(self) -> list[dict[str, Any]]:
        return group_monitor.status_pairs(list(self._group_addresses.values()), self._range_names)

    def set_dpt(self, group_address: str, dpt: str | None) -> dict[str, Any]:
        """Datentyp für eine GA festlegen (z. B. ohne ETS-Projekt); gilt ab dem nächsten Telegramm."""
        address = group_monitor.parse_group_address(group_address)
        if dpt and group_monitor.transcoder_for(dpt) is None:
            raise group_monitor.MonitorError(f"Unbekannter Datentyp «{dpt}».")
        row = dict(self._group_addresses.get(address) or {"address": address, "name": None, "size": None})
        row["dpt"] = dpt or None
        self._group_addresses[address] = row
        self._guessed_dpts.pop(address, None)
        self._apply_group_address_dpts()
        state = self.group_state.get(address)
        if state and state.get("hex") and dpt:
            # Letzten Wert sofort mit dem neuen Datentyp lesen, nicht erst beim nächsten Telegramm
            data = bytes.fromhex(state["hex"].replace(" ", ""))
            payload_value = DPTBinary(data[0]) if state.get("small") and len(data) == 1 else DPTArray(tuple(data))
            value = group_monitor.decode(dpt, payload_value)
            state.update({"dpt": dpt, "value_json": value, "dpt_guessed": False,
                          "unit": group_monitor.describe(dpt)["unit"],
                          "value": None if value is None else group_monitor.value_text(value, group_monitor.describe(dpt)["unit"])})
        return row | {"state": state}

    def send_mode(self) -> dict[str, Any]:
        remaining = max(0, round(self._send_enabled_until - time.monotonic()))
        return {"enabled": remaining > 0, "remaining_s": remaining}

    def set_send_mode(self, enabled: bool) -> dict[str, Any]:
        self._send_enabled_until = time.monotonic() + group_monitor.SEND_MODE_SECONDS if enabled else 0.0
        return self.send_mode()

    def _require_sending(self) -> None:
        if not self.connected or self.xknx is None:
            raise RuntimeError("Keine aktive KNX-Verbindung. Zuerst in Schritt 2 «Verbindung» mit der KNX-Schnittstelle verbinden.")
        if not self.send_mode()["enabled"]:
            raise PermissionError("Senden ist gesperrt. Oben «Nur beobachten» anklicken und Senden für 30 Minuten freigeben.")

    def _outgoing_item(self, destination: str, service: str, label: str, dpt: Any = None,
                       value: Any = None, hex_value: str = "") -> dict[str, Any]:
        row = self._group_addresses.get(destination, {})
        dpt = dpt or row.get("dpt")
        return {
            "time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "direction": "Outgoing",
            "source": str(self.xknx.current_address) if self.xknx is not None else "",
            "destination": destination,
            "group_name": row.get("name"),
            "dpt": dpt,
            "service": service,
            "value": None if value is None else str(value),
            "value_json": group_monitor.json_value(value),
            "unit": group_monitor.describe(dpt).get("unit"),
            "hex": hex_value,
            "raw": "",
            "secure": False,
            "origin": f"IBS-Monitor{': ' + label if label else ''}",
        }

    async def send_value(self, group_address: str, dpt: Any = None, value: Any = None, raw_hex: str | None = None,
                         raw_small: bool = False, label: str = "") -> dict[str, Any]:
        """GroupValueWrite mit beliebigem Datentyp senden (oder als Rohwert)."""
        self._require_sending()
        destination = group_monitor.parse_group_address(group_address)
        dpt = dpt or self._group_addresses.get(destination, {}).get("dpt")
        payload_value, shown = group_monitor.encode(dpt, value, raw_hex, raw_small)
        decoded = group_monitor.decode(dpt, payload_value) if not raw_hex else None
        await self.xknx.telegrams.put(Telegram(destination_address=GroupAddress(destination),
                                               payload=apci.GroupValueWrite(payload_value)))
        item = self._outgoing_item(destination, "GroupValueWrite", label, dpt,
                                   decoded if decoded is not None else shown, group_monitor.payload_hex(payload_value))
        item["small"] = isinstance(payload_value, DPTBinary)
        item["secure"] = destination in self.secure_groups()
        if item["secure"]:
            item["secure_warning"] = self._secure_send_warning(destination)
        self._remember(item)
        self._publish(item)
        return item

    def _secure_send_warning(self, destination: str) -> str | None:
        """Data-Secure-Geräte nehmen nur Telegramme von Absendern an, die in der ETS eingetragen sind."""
        status = self.data_secure_status()
        interface = status["current_interface"]
        keyring = self.data_secure_keyring
        if keyring is None:
            return None
        ia = self.xknx.current_address if self.xknx is not None else None
        allowed = ia is not None and any(
            ia in senders for ga, senders in self._keyring_group_senders(keyring).items() if str(ga) == destination)
        if allowed or (interface and interface["secure_group_count"]):
            return None
        return (f"Gesendet mit Quelladresse {status['current_address']}. Data-Secure-Geräte übernehmen den Wert nur, wenn diese "
                "Adresse in der ETS als Sender eingetragen ist – sonst eine Tunneladresse aus dem Schlüsselbund verwenden.")

    @staticmethod
    def _keyring_group_senders(keyring: Keyring) -> dict[Any, list[Any]]:
        senders: dict[Any, list[Any]] = {}
        for interface in keyring.interfaces:
            for ga, ias in interface.group_addresses.items():
                senders.setdefault(ga, []).extend(ias)
                if interface.individual_address is not None:
                    senders[ga].append(interface.individual_address)
        return senders

    async def send_read(self, group_address: str, label: str = "") -> dict[str, Any]:
        """GroupValueRead senden; die Antwort erscheint als GroupValueResponse im Monitor."""
        self._require_sending()
        destination = group_monitor.parse_group_address(group_address)
        await self.xknx.telegrams.put(Telegram(destination_address=GroupAddress(destination),
                                               payload=apci.GroupValueRead()))
        item = self._outgoing_item(destination, "GroupValueRead", label)
        self._publish(item)
        return item

    async def function_test(self, command: str, value: Any, status: str | None = None, dpt: Any = None,
                            timeout: float = 3.0, read_fallback: bool = True) -> dict[str, Any]:
        """Befehl senden und auf die Rückmeldung der Status-GA warten (bei Bedarf einmal nachlesen)."""
        command = group_monitor.parse_group_address(command)
        status = group_monitor.parse_group_address(status) if status else command
        timeout = min(max(float(timeout), 0.5), 15.0)
        queue = self.subscribe()
        started = time.monotonic()
        try:
            sent = await self.send_value(command, dpt, value, label="Funktionstest")
            status_dpt = self._group_addresses.get(status, {}).get("dpt")
            compare = status != command and group_monitor.same_kind(
                dpt or self._group_addresses.get(command, {}).get("dpt"), status_dpt)

            def matches(item: dict[str, Any]) -> bool:
                return not compare or item.get("value_json") is None or group_monitor.values_match(
                    sent.get("value_json"), item.get("value_json"), status_dpt or dpt)

            # Bis zur Wartezeit auf den passenden Wert warten – Storen und Dimmer melden Zwischenwerte
            reply, deadline = None, time.monotonic() + timeout
            while (remaining := deadline - time.monotonic()) > 0:
                item = await self._wait_for(queue, status, remaining)
                if item is None:
                    break
                reply = item
                if matches(item):
                    break
            read_used = False
            if reply is None and read_fallback:
                read_used = True
                await self.send_read(status, label="Funktionstest – Status lesen")
                reply = await self._wait_for(queue, status, min(timeout, 3.0))
        finally:
            self.unsubscribe(queue)
        result = {
            "command": command, "status": status, "sent": sent.get("value_json"),
            "received": reply.get("value_json") if reply else None,
            "received_text": reply.get("value") if reply else None,
            "source": reply.get("source") if reply else None,
            "latency_ms": round((time.monotonic() - started) * 1000) if reply else None,
            "read_used": read_used,
        }
        if reply is None:
            result["result"] = "no_reply"
            result["message"] = "Keine Rückmeldung – Status-GA, Verknüpfung im Aktor oder Busverbindung prüfen."
        elif matches(reply):
            result["result"] = "ok"
            result["message"] = "Rückmeldung erhalten" + (" (nach Lesen)" if read_used else "")
        else:
            result["result"] = "mismatch"
            result["message"] = "Rückmeldung weicht vom gesendeten Wert ab."
        return result

    async def _wait_for(self, queue: asyncio.Queue[dict[str, Any]], address: str, timeout: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                item = await asyncio.wait_for(queue.get(), remaining)
            except TimeoutError:
                return None
            if (item.get("destination") == address and item.get("direction") != "Outgoing"
                    and item.get("service") in {"GroupValueWrite", "GroupValueResponse"}):
                return item
        return None

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=250)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def _on_telegram(self, telegram: Telegram) -> None:
        payload = telegram.payload
        decoded = str(telegram.decoded_data) if telegram.decoded_data is not None else None
        destination = str(telegram.destination_address)
        group_address = self._group_addresses.get(destination, {})
        if telegram.decoded_data is not None and isinstance(telegram.decoded_data.value, bool):
            decoded = "1 · Ein/True" if telegram.decoded_data.value else "0 · Aus/False"
        transcoder = telegram.decoded_data.transcoder if telegram.decoded_data is not None else None
        guessed = None
        if not group_address.get("dpt") and transcoder is None and payload is not None:
            guessed = self._guessed_dpts.get(destination) or group_monitor.guess_dpt(
                group_address.get("name"), getattr(payload, "value", None))
            if guessed:
                self._guessed_dpts[destination] = guessed
        dpt = group_address.get("dpt") or guessed or (
            f"{transcoder.dpt_main_number}.{transcoder.dpt_sub_number:03d}"
            if transcoder is not None and getattr(transcoder, "dpt_sub_number", None) is not None else None)
        item = {
            "time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "direction": telegram.direction.value,
            "source": str(telegram.source_address),
            "destination": destination,
            "group_name": group_address.get("name"),
            "dpt": dpt,
            "service": type(payload).__name__ if payload is not None else type(telegram.tpci).__name__,
            "value": decoded,
            "value_json": group_monitor.json_value(telegram.decoded_data.value) if telegram.decoded_data
            else group_monitor.decode(dpt, getattr(payload, "value", None)),
            "unit": group_monitor.unit_of(transcoder) if transcoder is not None else group_monitor.describe(dpt)["unit"],
            "hex": group_monitor.payload_hex(getattr(payload, "value", None)),
            "small": isinstance(getattr(payload, "value", None), DPTBinary),
            "raw": str(payload) if payload is not None else "",
            "secure": telegram.data_secure is True,
            "dpt_guessed": bool(guessed),
        }
        if item["value"] is None and item["value_json"] is not None:
            item["value"] = group_monitor.value_text(item["value_json"], item["unit"])
        with suppress(sco.SCOError):
            self._attach_sco(item, payload)
        self._remember(item)
        self._publish(item)


bus_connection = BusConnection()
