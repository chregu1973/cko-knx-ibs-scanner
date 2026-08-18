"""Persistent local KNX/IP connection, device checks and telegram monitor."""

from __future__ import annotations

import asyncio
import ipaddress
from datetime import datetime
from typing import Any

from xknx import XKNX
from xknx.io import ConnectionConfig, ConnectionType
from xknx.management.procedures import nm_individual_address_check
from xknx.telegram import Telegram


class BusConnection:
    """Own the single KNX/IP tunnel used by the local application."""

    def __init__(self) -> None:
        self.xknx: XKNX | None = None
        self.gateway_ip: str | None = None
        self.local_ip: str | None = None
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._group_addresses: dict[str, dict[str, str | None]] = {}
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self.xknx is not None and self.xknx.started.is_set()

    async def connect(self, gateway_ip: str, local_ip: str | None = None) -> dict[str, Any]:
        gateway = str(ipaddress.ip_address(gateway_ip))
        local = str(ipaddress.ip_address(local_ip)) if local_ip else None
        async with self._lock:
            await self._disconnect_unlocked()
            config = ConnectionConfig(
                connection_type=ConnectionType.TUNNELING,
                gateway_ip=gateway,
                gateway_port=3671,
                local_ip=local,
                auto_reconnect=True,
            )
            candidate = XKNX(connection_config=config, telegram_received_cb=self._on_telegram)
            try:
                async with asyncio.timeout(10):
                    await candidate.start()
            except Exception:
                await candidate.stop()
                raise
            self.xknx = candidate
            self._apply_group_address_dpts()
            self.gateway_ip = gateway
            self.local_ip = local
        return self.status("KNX/IP-Tunnel dauerhaft aufgebaut. Busmonitor ist aktiv.")

    async def disconnect(self) -> dict[str, Any]:
        async with self._lock:
            await self._disconnect_unlocked()
        return self.status("KNX/IP-Verbindung getrennt.")

    async def _disconnect_unlocked(self) -> None:
        if self.xknx is not None:
            await self.xknx.stop()
        self.xknx = None
        self.gateway_ip = None
        self.local_ip = None

    async def check_device(self, address: str) -> bool:
        if not self.connected or self.xknx is None:
            raise RuntimeError("Keine aktive KNX/IP-Verbindung.")
        async with asyncio.timeout(6):
            return await nm_individual_address_check(self.xknx, address)

    def status(self, message: str | None = None) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "gateway_ip": self.gateway_ip,
            "local_ip": self.local_ip,
            "message": message,
        }

    def configure_group_addresses(self, rows: list[dict[str, str | None]]) -> None:
        """Keep ETS group address metadata in local memory for monitor enrichment."""
        self._group_addresses = {str(row["address"]): row for row in rows}
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
        item = {
            "time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "direction": telegram.direction.value,
            "source": str(telegram.source_address),
            "destination": destination,
            "group_name": group_address.get("name"),
            "dpt": group_address.get("dpt"),
            "service": type(payload).__name__ if payload is not None else type(telegram.tpci).__name__,
            "value": decoded,
            "raw": str(payload) if payload is not None else "",
            "secure": telegram.data_secure is True,
        }
        for queue in tuple(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(item)


bus_connection = BusConnection()
