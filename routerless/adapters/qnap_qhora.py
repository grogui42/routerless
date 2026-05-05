"""QNAP Qhora 301W adapter.

The Qhora 301W runs a firmware based on OpenWrt. If UCI is available
(which is typical), this adapter delegates entirely to the OpenWrt adapter
logic.  If UCI is absent (e.g. on a stock firmware variant without it), a
NotImplementedError is raised with guidance on enabling SSH and UCI.

SSH access must be enabled manually on the Qhora:
  Hold the WPS button on the back for ~12 seconds until the second beep.
  Default SSH port: 22200
"""
from __future__ import annotations

from routerless.adapters.base import BaseAdapter
from routerless.adapters.openwrt import OpenWrtAdapter
from routerless.models.config import (
    DHCPConfig,
    FirewallConfig,
    NATConfig,
    NetworkConfig,
    TargetConfig,
    TargetType,
)
from routerless.models.status import AdapterStatus, ConnectedDevice, WifiRadio

_UCI_CHECK_CMD = "command -v uci"


class QnapQhoraAdapter(BaseAdapter):
    """Manages QNAP Qhora 301W configuration via SSH.

    Delegates to :class:`~routerless.adapters.openwrt.OpenWrtAdapter` when
    UCI is available, which is the case on stock Qhora firmware (OpenWrt-based).
    """

    TARGET_TYPE = TargetType.QNAP_QHORA

    def __init__(self, target: TargetConfig) -> None:
        super().__init__(target)
        # Build an OpenWrt adapter with the same target but force ssh_port
        # to the Qhora default (22200) if not explicitly set to something else.
        openwrt_target = target.model_copy(
            update={"type": TargetType.OPENWRT}
        )
        self._openwrt = OpenWrtAdapter(openwrt_target)

    def _assert_uci_available(self) -> None:
        with self._openwrt._ssh() as client:
            _, stdout, _ = client.exec_command(_UCI_CHECK_CMD)
            exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            raise NotImplementedError(
                "UCI is not available on this Qhora firmware. "
                "Consider flashing OpenWrt (officially supported on QHora-301W) "
                "or using raw iptables/dnsmasq configuration."
            )

    def apply_dhcp(self, config: DHCPConfig) -> None:
        self._assert_uci_available()
        self._openwrt.apply_dhcp(config)

    def apply_nat(self, config: NATConfig) -> None:
        self._assert_uci_available()
        self._openwrt.apply_nat(config)

    def apply_firewall(self, config: FirewallConfig) -> None:
        self._assert_uci_available()
        self._openwrt.apply_firewall(config)

    def dump(self) -> NetworkConfig:
        self._assert_uci_available()
        return self._openwrt.dump()

    def get_status(self) -> AdapterStatus:
        self._assert_uci_available()
        return self._openwrt.get_status()

    def get_devices(self, only_active: bool = True) -> list[ConnectedDevice]:
        self._assert_uci_available()
        return self._openwrt.get_devices(only_active=only_active)

    def get_wifi(self) -> list[WifiRadio]:
        self._assert_uci_available()
        return self._openwrt.get_wifi()

    def wifi_enable(self, enable: bool) -> None:
        self._assert_uci_available()
        self._openwrt.wifi_enable(enable)
