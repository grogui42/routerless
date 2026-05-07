"""OpenWrt adapter — configuration via SSH + UCI commands."""
from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Generator

import paramiko

from routerless.adapters.base import BaseAdapter
from routerless.models.config import (
    DHCPConfig,
    FirewallAction,
    FirewallConfig,
    FirewallDirection,
    FirewallRule,
    NATConfig,
    NetworkConfig,
    PortForward,
    Protocol,
    StaticLease,
    TargetType,
)
from routerless.models.status import AdapterStatus, ConnectedDevice, WifiRadio


class OpenWrtAdapter(BaseAdapter):
    """Manages OpenWrt configuration via SSH and the UCI CLI."""

    TARGET_TYPE = TargetType.OPENWRT

    # ------------------------------------------------------------------
    # SSH connection management
    # ------------------------------------------------------------------

    @contextmanager
    def _ssh(self) -> Generator[paramiko.SSHClient, None, None]:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        connect_kwargs: dict = dict(
            hostname=self.target.host,
            port=self.target.ssh_port,
            username=self.target.ssh_user,
            timeout=15,
        )
        if self.target.ssh_key:
            connect_kwargs["key_filename"] = self.target.ssh_key
        else:
            connect_kwargs["password"] = self.target.ssh_password
            connect_kwargs["look_for_keys"] = False
            connect_kwargs["allow_agent"] = False
        client.connect(**connect_kwargs)
        try:
            yield client
        finally:
            client.close()

    def _run(self, client: paramiko.SSHClient, cmd: str) -> str:
        """Run a command and return stdout. Raises on non-zero exit."""
        _, stdout, stderr = client.exec_command(cmd)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode()
        err = stderr.read().decode()
        if exit_code != 0:
            raise RuntimeError(
                f"Command failed (exit {exit_code}): {cmd!r}\nstderr: {err.strip()}"
            )
        return out

    # ------------------------------------------------------------------
    # DHCP / Static leases
    # ------------------------------------------------------------------

    def apply_dhcp(self, config: DHCPConfig) -> None:
        with self._ssh() as client:
            # Read existing static lease MACs to avoid duplicates
            existing_macs = self._get_existing_lease_macs(client)
            new_leases = [
                lease for lease in config.static_leases
                if lease.mac.upper() not in existing_macs
            ]
            changed_leases = [
                lease for lease in config.static_leases
                if lease.mac.upper() in existing_macs
            ]

            # Add new leases
            for lease in new_leases:
                self._run(client, "uci add dhcp host")
                self._run(client, f"uci set dhcp.@host[-1].mac='{lease.mac}'")
                self._run(client, f"uci set dhcp.@host[-1].ip='{lease.ip}'")
                self._run(client, f"uci set dhcp.@host[-1].name='{lease.name}'")
                if lease.hostname:
                    self._run(client, "uci set dhcp.@host[-1].dns='1'")

            # Update changed leases
            for lease in changed_leases:
                idx = self._find_lease_index(client, lease.mac)
                if idx is not None:
                    self._run(client, f"uci set dhcp.@host[{idx}].ip='{lease.ip}'")
                    self._run(client, f"uci set dhcp.@host[{idx}].name='{lease.name}'")

            if new_leases or changed_leases:
                self._run(client, "uci commit dhcp")
                self._run(client, "/etc/init.d/dnsmasq restart")

    def _get_existing_lease_macs(self, client: paramiko.SSHClient) -> set[str]:
        try:
            out = self._run(client, "uci show dhcp | grep '@host.*\\.mac='")
        except RuntimeError:
            return set()
        macs: set[str] = set()
        for line in out.splitlines():
            if m := re.search(r"\.mac='([^']+)'", line):
                macs.add(m.group(1).upper())
        return macs

    def _find_lease_index(
        self, client: paramiko.SSHClient, mac: str
    ) -> int | None:
        try:
            out = self._run(client, "uci show dhcp | grep '@host.*\\.mac='")
        except RuntimeError:
            return None
        for line in out.splitlines():
            if m := re.search(r"@host\[(\d+)\]\.mac='([^']+)'", line):
                if m.group(2).upper() == mac.upper():
                    return int(m.group(1))
        return None

    # ------------------------------------------------------------------
    # NAT / Port forwarding
    # ------------------------------------------------------------------

    def apply_nat(self, config: NATConfig) -> None:
        with self._ssh() as client:
            existing = self._get_existing_redirects(client)  # {name: idx}
            desired_names = {pf.name for pf in config.port_forwards}
            changed = False

            # Delete stale entries - highest index first to avoid index shift
            stale_indices = sorted(
                [idx for name, idx in existing.items() if name not in desired_names],
                reverse=True,
            )
            for idx in stale_indices:
                self._run(client, f"uci delete firewall.@redirect[{idx}]")
                changed = True
            if stale_indices:
                existing = self._get_existing_redirects(client)

            for pf in config.port_forwards:
                if pf.name in existing:
                    self._apply_redirect_at(client, existing[pf.name], pf)
                    changed = True
                else:
                    self._run(client, "uci add firewall redirect")
                    self._run(client, f"uci set firewall.@redirect[-1].name='{pf.name}'")
                    self._run(client, "uci set firewall.@redirect[-1].target='DNAT'")
                    self._run(client, "uci set firewall.@redirect[-1].src='wan'")
                    self._run(client, "uci set firewall.@redirect[-1].dest='lan'")
                    proto = "tcp udp" if pf.protocol == Protocol.BOTH else pf.protocol.value
                    self._run(client, f"uci set firewall.@redirect[-1].proto='{proto}'")
                    self._run(client, f"uci set firewall.@redirect[-1].src_dport='{pf.external_port}'")
                    self._run(client, f"uci set firewall.@redirect[-1].dest_ip='{pf.internal_ip}'")
                    self._run(client, f"uci set firewall.@redirect[-1].dest_port='{pf.internal_port}'")
                    changed = True

            if changed:
                self._run(client, "uci commit firewall")
                self._run(client, "/etc/init.d/firewall restart")

    def _get_existing_redirects(self, client: paramiko.SSHClient) -> dict[str, int]:
        """Return {name: uci_index} for all existing firewall redirects."""
        try:
            out = self._run(client, "uci show firewall | grep '@redirect.*\\.name='")
        except RuntimeError:
            return {}
        result: dict[str, int] = {}
        for line in out.splitlines():
            if m := re.search(r"@redirect\[(\d+)\]\.name='([^']+)'", line):
                result[m.group(2)] = int(m.group(1))
        return result

    def _apply_redirect_at(
        self, client: paramiko.SSHClient, idx: int, pf: PortForward
    ) -> None:
        proto = "tcp udp" if pf.protocol == Protocol.BOTH else pf.protocol.value
        self._run(client, f"uci set firewall.@redirect[{idx}].proto='{proto}'")
        self._run(client, f"uci set firewall.@redirect[{idx}].src_dport='{pf.external_port}'")
        self._run(client, f"uci set firewall.@redirect[{idx}].dest_ip='{pf.internal_ip}'")
        self._run(client, f"uci set firewall.@redirect[{idx}].dest_port='{pf.internal_port}'")

    # ------------------------------------------------------------------
    # Firewall rules
    # ------------------------------------------------------------------

    def apply_firewall(self, config: FirewallConfig) -> None:
        with self._ssh() as client:
            existing = self._get_existing_rules(client)  # {name: idx}
            desired_names = {rule.name for rule in config.rules}
            changed = False

            # Delete stale entries - highest index first to avoid index shift
            stale_indices = sorted(
                [idx for name, idx in existing.items() if name not in desired_names],
                reverse=True,
            )
            for idx in stale_indices:
                self._run(client, f"uci delete firewall.@rule[{idx}]")
                changed = True
            if stale_indices:
                existing = self._get_existing_rules(client)

            for rule in config.rules:
                if rule.name in existing:
                    self._apply_rule_at(client, existing[rule.name], rule)
                    changed = True
                else:
                    self._run(client, "uci add firewall rule")
                    self._run(client, f"uci set firewall.@rule[-1].name='{rule.name}'")
                    self._apply_rule_at(client, -1, rule)
                    changed = True

            if changed:
                self._run(client, "uci commit firewall")
                self._run(client, "/etc/init.d/firewall restart")

    def _get_existing_rules(self, client: paramiko.SSHClient) -> dict[str, int]:
        """Return {name: uci_index} for all existing firewall rules."""
        try:
            out = self._run(client, "uci show firewall | grep '@rule.*\\.name='")
        except RuntimeError:
            return {}
        result: dict[str, int] = {}
        for line in out.splitlines():
            if m := re.search(r"@rule\[(\d+)\]\.name='([^']+)'", line):
                result[m.group(2)] = int(m.group(1))
        return result

    def _apply_rule_at(
        self, client: paramiko.SSHClient, idx: int, rule: FirewallRule
    ) -> None:
        ref = f"firewall.@rule[{idx}]"
        # OpenWrt UCI uses src/dest zone names for directionality, not a direction field
        target_map = {
            FirewallAction.ACCEPT: "ACCEPT",
            FirewallAction.DROP: "DROP",
            FirewallAction.REJECT: "REJECT",
        }
        self._run(client, f"uci set {ref}.target='{target_map[rule.action]}'")
        if rule.src:
            self._run(client, f"uci set {ref}.src='{rule.src}'")
        if rule.dest:
            self._run(client, f"uci set {ref}.dest='{rule.dest}'")
        if rule.src_ip:
            self._run(client, f"uci set {ref}.src_ip='{rule.src_ip}'")
        if rule.dest_ip:
            self._run(client, f"uci set {ref}.dest_ip='{rule.dest_ip}'")
        if rule.dest_port:
            self._run(client, f"uci set {ref}.dest_port='{rule.dest_port}'")
        if rule.protocol:
            proto = "tcp udp" if rule.protocol == Protocol.BOTH else rule.protocol.value
            self._run(client, f"uci set {ref}.proto='{proto}'")

    # ------------------------------------------------------------------
    # Dump
    # ------------------------------------------------------------------

    def dump(self) -> NetworkConfig:
        with self._ssh() as client:
            dhcp_raw = self._run(client, "uci show dhcp")
            firewall_raw = self._run(client, "uci show firewall")

        leases = _parse_uci_host_leases(dhcp_raw)
        port_forwards = _parse_uci_redirects(firewall_raw)
        fw_rules = _parse_uci_rules(firewall_raw)

        from routerless.models.config import DHCPConfig, FirewallConfig, NATConfig
        return NetworkConfig(
            dhcp=DHCPConfig(
                subnet="0.0.0.0/0",
                gateway="0.0.0.0",  # noqa: S104
                static_leases=leases,
            ) if leases else None,
            nat=NATConfig(port_forwards=port_forwards) if port_forwards else None,
            firewall=FirewallConfig(rules=fw_rules) if fw_rules else None,
            targets={self.target.host: self.target},
        )

    # ------------------------------------------------------------------
    # get_status
    # ------------------------------------------------------------------

    def get_status(self) -> AdapterStatus:
        with self._ssh() as client:
            try:
                model = self._run(client, "uci get system.@system[0].hostname").strip()
            except RuntimeError:
                model = "OpenWrt"

            uptime_seconds = 0
            try:
                uptime_raw = self._run(client, "cat /proc/uptime").strip()
                uptime_seconds = int(float(uptime_raw.split()[0]))
            except (RuntimeError, ValueError, IndexError):
                pass

            lan_ip = ""
            for cmd in (
                "uci get network.lan.ipaddr",
                "uci get network.lan.ipaddress",
            ):
                try:
                    val = self._run(client, cmd).strip().split()[0]
                    if val:
                        lan_ip = val
                        break
                except (RuntimeError, IndexError):
                    continue

            wan_ip = ""
            for cmd in (
                "uci get network.wan.ipaddr",
                "uci get network.wan.ipaddress",
            ):
                try:
                    val = self._run(client, cmd).strip().split()[0]
                    if val:
                        wan_ip = val
                        break
                except (RuntimeError, IndexError):
                    continue

            device_count = 0
            try:
                leases_raw = self._run(client, "cat /tmp/dhcp.leases")
                device_count = sum(1 for line in leases_raw.splitlines() if line.strip())
            except RuntimeError:
                pass

            wifi_24_enabled: bool | None = None
            wifi_5_enabled: bool | None = None
            try:
                wireless_raw = self._run(client, "uci show wireless")
                wifi_24_enabled, wifi_5_enabled = _parse_wifi_enabled(wireless_raw)
            except RuntimeError:
                pass

        return AdapterStatus(
            lan_ip=lan_ip,
            wan_ip=wan_ip,
            uptime_seconds=uptime_seconds,
            device_count=device_count,
            model=model,
            wifi_24_enabled=wifi_24_enabled,
            wifi_5_enabled=wifi_5_enabled,
        )

    # ------------------------------------------------------------------
    # get_devices
    # ------------------------------------------------------------------

    def get_devices(self, only_active: bool = True) -> list[ConnectedDevice]:
        with self._ssh() as client:
            active_ips: set[str] = set()
            try:
                arp_raw = self._run(client, "cat /proc/net/arp")
                for line in arp_raw.splitlines()[1:]:  # skip header
                    parts = line.split()
                    if len(parts) >= 4 and parts[2] not in ("0x0", "0x00000000"):
                        active_ips.add(parts[0])
            except RuntimeError:
                pass

            devices: list[ConnectedDevice] = []
            try:
                leases_raw = self._run(client, "cat /tmp/dhcp.leases")
                for line in leases_raw.splitlines():
                    parts = line.strip().split()
                    if len(parts) < 4:
                        continue
                    mac = parts[1].upper()
                    ip = parts[2]
                    hostname = parts[3] if parts[3] != "*" else ""
                    active = ip in active_ips
                    if only_active and not active:
                        continue
                    devices.append(ConnectedDevice(ip=ip, mac=mac, hostname=hostname, active=active))
            except RuntimeError:
                pass

        return devices

    # ------------------------------------------------------------------
    # get_wifi
    # ------------------------------------------------------------------

    def get_wifi(self) -> list[WifiRadio]:
        with self._ssh() as client:
            try:
                wireless_raw = self._run(client, "uci show wireless")
            except RuntimeError:
                return []

        radios: dict[str, dict[str, str]] = {}
        ifaces: dict[str, dict[str, str]] = {}
        for line in wireless_raw.splitlines():
            if m := re.match(r"wireless\.(radio\d+)\.(\w+)='([^']*)'", line):
                rid, key, val = m.group(1), m.group(2), m.group(3)
                radios.setdefault(rid, {})[key] = val
            elif m := re.match(r"wireless\.@wifi-iface\[(\d+)\]\.(\w+)='([^']*)'", line):
                idx, key, val = m.group(1), m.group(2), m.group(3)
                ifaces.setdefault(idx, {})[key] = val

        radio_ssid: dict[str, str] = {}
        radio_enc: dict[str, str] = {}
        for iface in ifaces.values():
            dev = iface.get("device", "")
            if dev:
                radio_ssid.setdefault(dev, iface.get("ssid", ""))
                radio_enc.setdefault(dev, iface.get("encryption", ""))

        result: list[WifiRadio] = []
        for rid, data in sorted(radios.items()):
            band_raw = data.get("band", data.get("hwmode", ""))
            if "2g" in band_raw or "11g" in band_raw or "11b" in band_raw:
                band = "2.4GHz"
            elif "5g" in band_raw or "11a" in band_raw:
                band = "5GHz"
            else:
                band = band_raw or rid
            enabled = data.get("disabled", "0") != "1"
            try:
                channel = int(data.get("channel", 0)) or None
            except ValueError:
                channel = None
            result.append(WifiRadio(
                band=band,
                enabled=enabled,
                ssid=radio_ssid.get(rid, ""),
                channel=channel,
                encryption=radio_enc.get(rid, ""),
            ))
        return result

    # ------------------------------------------------------------------
    # wifi_enable
    # ------------------------------------------------------------------

    def wifi_enable(self, enable: bool) -> None:
        with self._ssh() as client:
            try:
                wireless_raw = self._run(client, "uci show wireless")
            except RuntimeError:
                return
            radio_ids: set[str] = set()
            for line in wireless_raw.splitlines():
                if m := re.match(r"wireless\.(radio\d+)\.", line):
                    radio_ids.add(m.group(1))
            val = "0" if enable else "1"
            for rid in sorted(radio_ids):
                self._run(client, f"uci set wireless.{rid}.disabled={val}")
            if radio_ids:
                self._run(client, "uci commit wireless")
                self._run(client, "wifi")



# ---------------------------------------------------------------------------
# UCI output parsers
# ---------------------------------------------------------------------------

def _parse_uci_host_leases(raw: str) -> list[StaticLease]:
    leases: dict[int, dict] = {}
    for line in raw.splitlines():
        if m := re.match(r"dhcp\.@host\[(\d+)\]\.(\w+)='([^']*)'", line):
            idx, key, val = int(m.group(1)), m.group(2), m.group(3)
            leases.setdefault(idx, {})[key] = val
    result = []
    for data in leases.values():
        if "mac" in data and "ip" in data:
            result.append(
                StaticLease(
                    name=data.get("name", data["mac"]),
                    mac=data["mac"],
                    ip=data["ip"],
                    hostname=data.get("name"),
                )
            )
    return result


def _parse_uci_redirects(raw: str) -> list[PortForward]:
    redirects: dict[int, dict] = {}
    for line in raw.splitlines():
        if m := re.match(r"firewall\.@redirect\[(\d+)\]\.(\w+)='([^']*)'", line):
            idx, key, val = int(m.group(1)), m.group(2), m.group(3)
            redirects.setdefault(idx, {})[key] = val
    result = []
    for data in redirects.values():
        if data.get("target") == "DNAT":
            proto_raw = data.get("proto", "tcp")
            proto = Protocol.BOTH if "udp" in proto_raw and "tcp" in proto_raw else Protocol(proto_raw.split()[0])
            try:
                result.append(
                    PortForward(
                        name=data.get("name", f"redirect-{len(result)}"),
                        protocol=proto,
                        external_port=int(data["src_dport"]),
                        internal_ip=data["dest_ip"],
                        internal_port=int(data.get("dest_port", data["src_dport"])),
                    )
                )
            except (KeyError, ValueError):
                pass
    return result


def _parse_uci_rules(raw: str) -> list[FirewallRule]:
    rules: dict[int, dict] = {}
    for line in raw.splitlines():
        if m := re.match(r"firewall\.@rule\[(\d+)\]\.(\w+)='([^']*)'", line):
            idx, key, val = int(m.group(1)), m.group(2), m.group(3)
            rules.setdefault(idx, {})[key] = val
    result = []
    action_map = {"ACCEPT": FirewallAction.ACCEPT, "DROP": FirewallAction.DROP, "REJECT": FirewallAction.REJECT}
    for data in rules.values():
        action = action_map.get(data.get("target", "DROP"), FirewallAction.DROP)
        direction_raw = data.get("direction", data.get("src", "forward"))
        try:
            direction = FirewallDirection(direction_raw)
        except ValueError:
            direction = FirewallDirection.FORWARD
        result.append(
            FirewallRule(
                name=data.get("name", f"rule-{len(result)}"),
                direction=direction,
                src=data.get("src"),
                dest=data.get("dest"),
                src_ip=data.get("src_ip"),
                dest_ip=data.get("dest_ip"),
                dest_port=int(data["dest_port"]) if "dest_port" in data else None,
                action=action,
            )
        )
    return result


def _parse_wifi_enabled(raw: str) -> tuple[bool | None, bool | None]:
    """Parse 'uci show wireless' output → (wifi_24_enabled, wifi_5_enabled)."""
    radio_disabled: dict[str, str] = {}
    radio_band: dict[str, str] = {}
    for line in raw.splitlines():
        if m := re.match(r"wireless\.(radio\d+)\.disabled='([^']*)'", line):
            radio_disabled[m.group(1)] = m.group(2)
        elif m := re.match(r"wireless\.(radio\d+)\.(?:band|hwmode)='([^']*)'", line):
            radio_band[m.group(1)] = m.group(2)
    wifi_24: bool | None = None
    wifi_5: bool | None = None
    for radio_id, band_raw in radio_band.items():
        enabled = radio_disabled.get(radio_id, "0") != "1"
        if "2g" in band_raw or "11g" in band_raw or "11b" in band_raw:
            wifi_24 = enabled
        elif "5g" in band_raw or "11a" in band_raw:
            wifi_5 = enabled
    return wifi_24, wifi_5
