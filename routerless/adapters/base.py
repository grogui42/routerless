"""Abstract base class for all router adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from routerless.models.status import AdapterStatus, ConnectedDevice, WifiRadio

if TYPE_CHECKING:
    from routerless.models.config import (
        DHCPConfig,
        FirewallConfig,
        NATConfig,
        NetworkConfig,
        TargetConfig,
    )


class BaseAdapter(ABC):
    """Common interface every router adapter must implement."""

    def __init__(self, target: "TargetConfig") -> None:
        self.target = target

    # ------------------------------------------------------------------
    # Apply methods — called individually by the CLI --section flag
    # ------------------------------------------------------------------

    @abstractmethod
    def apply_dhcp(self, config: "DHCPConfig") -> None:
        """Apply DHCP server settings and static lease reservations."""

    @abstractmethod
    def apply_nat(self, config: "NATConfig") -> None:
        """Apply NAT / port-forwarding rules."""

    @abstractmethod
    def apply_firewall(self, config: "FirewallConfig") -> None:
        """Apply firewall rules."""

    # ------------------------------------------------------------------
    # Dump — read current running config from device
    # ------------------------------------------------------------------

    @abstractmethod
    def dump(self) -> "NetworkConfig":
        """Read the current configuration from the device and return it
        as a :class:`~routerless.models.config.NetworkConfig`.
        """

    # ------------------------------------------------------------------
    # Convenience: apply all sections at once
    # ------------------------------------------------------------------

    def apply_all(self, config: "NetworkConfig") -> None:
        """Apply all present sections from *config*."""
        if config.dhcp is not None:
            self.apply_dhcp(config.dhcp)
        if config.nat is not None:
            self.apply_nat(config.nat)
        if config.firewall is not None:
            self.apply_firewall(config.firewall)

    # ------------------------------------------------------------------
    # Optional read-only commands (raise NotImplementedError if unsupported)
    # ------------------------------------------------------------------

    def get_status(self) -> AdapterStatus:
        raise NotImplementedError(
            f"{type(self).__name__} does not implement get_status(). "
            "Only Bbox Ultim and OpenWrt adapters support this command."
        )

    def get_devices(self, only_active: bool = True) -> list[ConnectedDevice]:
        raise NotImplementedError(
            f"{type(self).__name__} does not implement get_devices(). "
            "Only Bbox Ultim and OpenWrt adapters support this command."
        )

    def get_wifi(self) -> list[WifiRadio]:
        raise NotImplementedError(
            f"{type(self).__name__} does not implement get_wifi(). "
            "Only Bbox Ultim and OpenWrt adapters support this command."
        )

    def wifi_enable(self, enable: bool) -> None:
        raise NotImplementedError(
            f"{type(self).__name__} does not implement wifi_enable(). "
            "Only Bbox Ultim and OpenWrt adapters support this command."
        )
