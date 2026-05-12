"""Pydantic models for routerless network configuration."""
from __future__ import annotations

from enum import Enum
from ipaddress import IPv4Address, IPv4Network
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Helpers / enums
# ---------------------------------------------------------------------------

class TargetType(str, Enum):
    BBOX_ULTIM = "bbox_ultim"
    OPENWRT = "openwrt"
    QNAP_QHORA = "qnap_qhora"
    FREEBOX = "freebox"


class Protocol(str, Enum):
    TCP = "tcp"
    UDP = "udp"
    BOTH = "both"


class FirewallAction(str, Enum):
    ACCEPT = "ACCEPT"
    DROP = "DROP"
    REJECT = "REJECT"


class FirewallDirection(str, Enum):
    INPUT = "input"
    OUTPUT = "output"
    FORWARD = "forward"


# ---------------------------------------------------------------------------
# Target (device credentials)
# ---------------------------------------------------------------------------

class TargetConfig(BaseModel):
    type: TargetType
    host: str
    # HTTP-based targets (Bbox, Freebox)
    password: str | None = None  # Bbox password or legacy Freebox field
    app_token: str | None = None  # Freebox app_token (preferred over password)
    verify_ssl: bool = True  # Validate SSL certificates (default: True)
    # SSH-based targets (OpenWrt, Qhora)
    ssh_user: str | None = None
    ssh_password: str | None = None
    ssh_key: str | None = None
    ssh_port: int = 22

    @model_validator(mode="after")
    def _check_credentials(self) -> "TargetConfig":
        if self.type == TargetType.BBOX_ULTIM and self.password is None:
            raise ValueError(f"{self.type.value} target requires 'password'")
        if self.type == TargetType.FREEBOX and self.app_token is None:
            raise ValueError(f"{self.type.value} target requires 'app_token'")
        if self.type in (TargetType.OPENWRT, TargetType.QNAP_QHORA):
            if self.ssh_user is None:
                raise ValueError(f"{self.type.value} target requires 'ssh_user'")
            if self.ssh_password is None and self.ssh_key is None:
                raise ValueError(
                    f"{self.type.value} target requires 'ssh_password' or 'ssh_key'"
                )
        return self


# ---------------------------------------------------------------------------
# DHCP
# ---------------------------------------------------------------------------

class StaticLease(BaseModel):
    name: str
    mac: str
    ip: str
    hostname: str | None = None

    @field_validator("mac")
    @classmethod
    def _validate_mac(cls, v: str) -> str:
        parts = v.upper().replace("-", ":").split(":")
        if len(parts) != 6 or not all(len(p) == 2 and all(c in "0123456789ABCDEF" for c in p) for p in parts):
            raise ValueError(f"Invalid MAC address: {v}")
        return ":".join(parts)

    @field_validator("ip")
    @classmethod
    def _validate_ip(cls, v: str) -> str:
        IPv4Address(v)  # raises if invalid
        return v


class DHCPConfig(BaseModel):
    subnet: str
    gateway: str
    dns: list[str] = Field(default_factory=list)
    range: dict[str, str] | None = None
    lease_time: str = "24h"
    static_leases: list[StaticLease] = Field(default_factory=list)

    @field_validator("subnet")
    @classmethod
    def _validate_subnet(cls, v: str) -> str:
        IPv4Network(v, strict=False)
        return v

    @field_validator("gateway")
    @classmethod
    def _validate_gateway(cls, v: str) -> str:
        IPv4Address(v)
        return v


# ---------------------------------------------------------------------------
# NAT / Port forwarding
# ---------------------------------------------------------------------------

class PortForward(BaseModel):
    name: str
    protocol: Protocol = Protocol.TCP
    external_port: int = Field(ge=1, le=65535)
    internal_ip: str
    internal_port: int = Field(ge=1, le=65535)
    external_ip: str | None = None  # optional: bind to specific WAN IP

    @field_validator("internal_ip")
    @classmethod
    def _validate_ip(cls, v: str) -> str:
        IPv4Address(v)
        return v


class NATConfig(BaseModel):
    port_forwards: list[PortForward] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Firewall
# ---------------------------------------------------------------------------

class FirewallRule(BaseModel):
    name: str
    direction: FirewallDirection = FirewallDirection.FORWARD
    src: str | None = None
    dest: str | None = None
    src_ip: str | None = None
    dest_ip: str | None = None
    dest_port: int | None = Field(default=None, ge=1, le=65535)
    protocol: Protocol | None = None
    action: FirewallAction = FirewallAction.DROP


class FirewallConfig(BaseModel):
    rules: list[FirewallRule] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------

class NetworkConfig(BaseModel):
    version: str = "1.0"
    targets: dict[str, TargetConfig] = Field(default_factory=dict)
    dhcp: DHCPConfig | None = None
    nat: NATConfig | None = None
    firewall: FirewallConfig | None = None


def parse_config(raw: dict[str, Any]) -> NetworkConfig:
    """Validate a raw dict (from yaml_loader.load_config) into a NetworkConfig.

    Args:
        raw: The fully-resolved configuration dictionary.

    Returns:
        A validated :class:`NetworkConfig` instance.

    Raises:
        pydantic.ValidationError: If the configuration is invalid.
    """
    return NetworkConfig.model_validate(raw)
