"""KNXnet/IP discovery and local connection checks."""

from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import asdict, dataclass

import ifaddr
from xknx import XKNX
from xknx.io import ConnectionConfig, ConnectionType, GatewayScanner


@dataclass(slots=True)
class GatewayInfo:
    name: str
    ip_address: str
    port: int
    individual_address: str | None
    local_ip: str
    local_interface: str
    supports_tunnelling: bool
    supports_routing: bool
    supports_secure: bool
    tunnelling_requires_secure: bool | None
    routing_requires_secure: bool | None
    serial_number: str
    mac_address: str

    def to_dict(self) -> dict:
        return asdict(self)


async def discover_gateways(local_ip: str | None = None, timeout: float = 3.0) -> list[GatewayInfo]:
    """Discover KNXnet/IP interfaces visible from a local network adapter."""
    xknx = XKNX()
    scanner = GatewayScanner(xknx=xknx, local_ip=local_ip, timeout_in_seconds=timeout)
    found: list[GatewayInfo] = []
    async for gateway in scanner.async_scan():
        found.append(
            GatewayInfo(
                name=gateway.name or "Unbekannte KNX/IP-Schnittstelle",
                ip_address=gateway.ip_addr,
                port=gateway.port,
                individual_address=str(gateway.individual_address) if gateway.individual_address else None,
                local_ip=gateway.local_ip,
                local_interface=gateway.local_interface,
                supports_tunnelling=gateway.supports_tunnelling,
                supports_routing=gateway.supports_routing,
                supports_secure=gateway.supports_secure,
                tunnelling_requires_secure=gateway.tunnelling_requires_secure,
                routing_requires_secure=gateway.routing_requires_secure,
                serial_number=gateway.serial_number,
                mac_address=gateway.mac_address,
            )
        )
    return found


def network_adapters() -> list[dict[str, str]]:
    """Return usable local IPv4 adapters for an explicit KNX/IP search."""
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for adapter in ifaddr.get_adapters():
        for address in adapter.ips:
            if not isinstance(address.ip, str):
                continue
            ip = ipaddress.ip_address(address.ip)
            if ip.version != 4 or ip.is_loopback or ip.is_unspecified or address.ip in seen:
                continue
            seen.add(address.ip)
            result.append({"name": adapter.nice_name, "ip_address": address.ip})
    return sorted(result, key=lambda item: (item["name"].lower(), item["ip_address"]))


async def test_tunnelling_connection(gateway_ip: str, local_ip: str | None = None) -> dict:
    """Open and immediately close a KNXnet/IP tunnel without changing bus data."""
    gateway = str(ipaddress.ip_address(gateway_ip))
    local = str(ipaddress.ip_address(local_ip)) if local_ip else None
    config = ConnectionConfig(
        connection_type=ConnectionType.TUNNELING,
        gateway_ip=gateway,
        gateway_port=3671,
        local_ip=local,
        auto_reconnect=False,
    )
    xknx = XKNX(connection_config=config)
    try:
        async with asyncio.timeout(10):
            await xknx.start()
        return {
            "connected": True,
            "gateway_ip": gateway,
            "local_ip": local,
            "message": "KNX/IP-Tunnel erfolgreich aufgebaut.",
        }
    finally:
        await xknx.stop()
