"""KNXnet/IP gateway discovery."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from xknx import XKNX
from xknx.io import GatewayScanner


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

