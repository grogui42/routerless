"""Generic read-only status dataclasses shared across all adapters."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AdapterStatus:
    """Summary status returned by any adapter's get_status()."""
    lan_ip: str
    uptime_seconds: int
    device_count: int
    model: str = ""
    serial: str = ""
    wan_ip: str = ""
    internet_state: str = ""
    voip_status: str = ""
    wifi_24_enabled: bool | None = None
    wifi_5_enabled: bool | None = None


@dataclass
class ConnectedDevice:
    """A host/device visible to the router."""
    ip: str
    mac: str
    hostname: str
    active: bool
    device_type: str = ""
    link: str = ""


@dataclass
class WifiRadio:
    """Status of one WiFi radio band."""
    band: str
    enabled: bool
    ssid: str
    channel: int | None = None
    protocol: str = ""
    encryption: str = ""
    device_count: int | None = None
