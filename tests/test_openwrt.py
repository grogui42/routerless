"""Tests for the OpenWrt adapter — SSH is mocked."""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from routerless.adapters.openwrt import (
    OpenWrtAdapter,
    _parse_uci_host_leases,
    _parse_uci_redirects,
    _parse_uci_rules,
    _parse_wifi_enabled,
)
from routerless.models.config import (
    DHCPConfig,
    FirewallConfig,
    FirewallRule,
    NATConfig,
    PortForward,
    StaticLease,
    TargetConfig,
    TargetType,
)
from routerless.models.status import AdapterStatus, ConnectedDevice, WifiRadio

TARGET = TargetConfig(
    type=TargetType.OPENWRT,
    host="192.168.1.1",
    ssh_user="root",
    ssh_password="pass",
    ssh_port=22,
)


def _make_adapter() -> OpenWrtAdapter:
    return OpenWrtAdapter(TARGET)


def _mock_ssh_client(run_outputs: dict[str, str] | None = None) -> MagicMock:
    """Return a mock SSHClient where exec_command returns preset outputs."""
    run_outputs = run_outputs or {}
    client = MagicMock()

    def exec_command(cmd: str):
        stdout = MagicMock()
        stderr = MagicMock()
        stdout.channel.recv_exit_status.return_value = 0
        stdout.read.return_value = run_outputs.get(cmd, b"")
        stderr.read.return_value = b""
        return MagicMock(), stdout, stderr

    client.exec_command.side_effect = exec_command
    return client


# ---------------------------------------------------------------------------
# UCI output parsers
# ---------------------------------------------------------------------------

class TestParseUciHostLeases:
    def test_parses_single_lease(self) -> None:
        raw = (
            "dhcp.@host[0]=host\n"
            "dhcp.@host[0].mac='AA:BB:CC:DD:EE:FF'\n"
            "dhcp.@host[0].ip='192.168.1.10'\n"
            "dhcp.@host[0].name='Hub'\n"
        )
        leases = _parse_uci_host_leases(raw)
        assert len(leases) == 1
        assert leases[0].mac == "AA:BB:CC:DD:EE:FF"
        assert leases[0].ip == "192.168.1.10"

    def test_parses_multiple_leases(self) -> None:
        raw = (
            "dhcp.@host[0].mac='AA:BB:CC:DD:EE:FF'\n"
            "dhcp.@host[0].ip='192.168.1.10'\n"
            "dhcp.@host[1].mac='11:22:33:44:55:66'\n"
            "dhcp.@host[1].ip='192.168.1.11'\n"
        )
        leases = _parse_uci_host_leases(raw)
        assert len(leases) == 2

    def test_skips_incomplete_entry(self) -> None:
        raw = "dhcp.@host[0].mac='AA:BB:CC:DD:EE:FF'\n"  # no ip
        leases = _parse_uci_host_leases(raw)
        assert leases == []


class TestParseUciRedirects:
    def test_parses_dnat(self) -> None:
        raw = (
            "firewall.@redirect[0]=redirect\n"
            "firewall.@redirect[0].name='HA'\n"
            "firewall.@redirect[0].target='DNAT'\n"
            "firewall.@redirect[0].proto='tcp'\n"
            "firewall.@redirect[0].src_dport='8123'\n"
            "firewall.@redirect[0].dest_ip='192.168.1.20'\n"
            "firewall.@redirect[0].dest_port='8123'\n"
        )
        pfs = _parse_uci_redirects(raw)
        assert len(pfs) == 1
        assert pfs[0].name == "HA"
        assert pfs[0].external_port == 8123

    def test_skips_non_dnat(self) -> None:
        raw = (
            "firewall.@redirect[0].target='SNAT'\n"
            "firewall.@redirect[0].src_dport='80'\n"
            "firewall.@redirect[0].dest_ip='10.0.0.1'\n"
        )
        pfs = _parse_uci_redirects(raw)
        assert pfs == []


class TestParseUciRules:
    def test_parses_rule(self) -> None:
        raw = (
            "firewall.@rule[0]=rule\n"
            "firewall.@rule[0].name='Block IoT'\n"
            "firewall.@rule[0].target='DROP'\n"
            "firewall.@rule[0].src='iot'\n"
            "firewall.@rule[0].dest='wan'\n"
        )
        rules = _parse_uci_rules(raw)
        assert len(rules) == 1
        assert rules[0].name == "Block IoT"


# ---------------------------------------------------------------------------
# apply_dhcp — verifies correct UCI commands are sent
# ---------------------------------------------------------------------------

class TestApplyDhcp:
    def test_new_lease_sends_uci_commands(self) -> None:
        adapter = _make_adapter()
        config = DHCPConfig(
            subnet="192.168.1.0/24",
            gateway="192.168.1.1",
            static_leases=[
                StaticLease(name="Hub", mac="AA:BB:CC:DD:EE:FF", ip="192.168.1.10")
            ],
        )
        mock_client = _mock_ssh_client({
            "uci show dhcp | grep '@host.*\\.mac='": b"",
        })

        with patch.object(adapter, "_ssh") as mock_ssh_ctx:
            mock_ssh_ctx.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_ssh_ctx.return_value.__exit__ = MagicMock(return_value=False)
            adapter.apply_dhcp(config)

        cmds = [c.args[0] for c in mock_client.exec_command.call_args_list]
        assert "uci add dhcp host" in cmds
        assert any("AA:BB:CC:DD:EE:FF" in c for c in cmds)
        assert any("192.168.1.10" in c for c in cmds)
        assert "uci commit dhcp" in cmds
        assert "/etc/init.d/dnsmasq restart" in cmds

    def test_existing_lease_not_duplicated(self) -> None:
        adapter = _make_adapter()
        config = DHCPConfig(
            subnet="192.168.1.0/24",
            gateway="192.168.1.1",
            static_leases=[
                StaticLease(name="Hub", mac="AA:BB:CC:DD:EE:FF", ip="192.168.1.10")
            ],
        )
        existing_output = b"dhcp.@host[0].mac='AA:BB:CC:DD:EE:FF'\n"
        mock_client = _mock_ssh_client({
            "uci show dhcp | grep '@host.*\\.mac='": existing_output,
            "uci show dhcp | grep '@host.*\\.mac='": existing_output,
        })

        with patch.object(adapter, "_ssh") as mock_ssh_ctx:
            mock_ssh_ctx.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_ssh_ctx.return_value.__exit__ = MagicMock(return_value=False)
            adapter.apply_dhcp(config)

        cmds = [c.args[0] for c in mock_client.exec_command.call_args_list]
        assert "uci add dhcp host" not in cmds


# ---------------------------------------------------------------------------
# apply_nat
# ---------------------------------------------------------------------------

class TestApplyNat:
    def test_new_port_forward_sends_uci_commands(self) -> None:
        adapter = _make_adapter()
        config = NATConfig(
            port_forwards=[
                PortForward(name="HA", external_port=8123, internal_ip="192.168.1.20", internal_port=8123)
            ]
        )
        mock_client = _mock_ssh_client({
            "uci show firewall | grep '@redirect.*\\.name='": b"",
        })

        with patch.object(adapter, "_ssh") as mock_ssh_ctx:
            mock_ssh_ctx.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_ssh_ctx.return_value.__exit__ = MagicMock(return_value=False)
            adapter.apply_nat(config)

        cmds = [c.args[0] for c in mock_client.exec_command.call_args_list]
        assert "uci add firewall redirect" in cmds
        assert any("8123" in c for c in cmds)
        assert any("192.168.1.20" in c for c in cmds)
        assert "uci commit firewall" in cmds


# ---------------------------------------------------------------------------
# _parse_wifi_enabled
# ---------------------------------------------------------------------------

class TestParseWifiEnabled:
    def test_2g_enabled(self) -> None:
        raw = (
            "wireless.radio0=wifi-device\n"
            "wireless.radio0.band='2g'\n"
            "wireless.radio0.disabled='0'\n"
        )
        wifi_24, wifi_5 = _parse_wifi_enabled(raw)
        assert wifi_24 is True
        assert wifi_5 is None

    def test_5g_disabled(self) -> None:
        raw = (
            "wireless.radio0.hwmode='11a'\n"
            "wireless.radio0.disabled='1'\n"
        )
        wifi_24, wifi_5 = _parse_wifi_enabled(raw)
        assert wifi_24 is None
        assert wifi_5 is False

    def test_both_radios(self) -> None:
        raw = (
            "wireless.radio0.band='2g'\n"
            "wireless.radio0.disabled='0'\n"
            "wireless.radio1.band='5g'\n"
            "wireless.radio1.disabled='1'\n"
        )
        wifi_24, wifi_5 = _parse_wifi_enabled(raw)
        assert wifi_24 is True
        assert wifi_5 is False


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------

_WIRELESS_RAW = (
    b"wireless.radio0=wifi-device\n"
    b"wireless.radio0.band='2g'\n"
    b"wireless.radio0.disabled='0'\n"
    b"wireless.radio1=wifi-device\n"
    b"wireless.radio1.band='5g'\n"
    b"wireless.radio1.disabled='1'\n"
)


def _ssh_ctx(mock_client: MagicMock):
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=mock_client)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


class TestGetStatus:
    def test_returns_adapter_status(self) -> None:
        adapter = _make_adapter()
        mock_client = _mock_ssh_client({
            "uci get system.@system[0].hostname": b"myrouter",
            "cat /proc/uptime": b"7200.5 3600.0",
            "uci get network.lan.ipaddr": b"192.168.1.1",
            "uci get network.wan.ipaddr": b"1.2.3.4",
            "cat /tmp/dhcp.leases": b"1234 AA:BB:CC:DD:EE:FF 192.168.1.10 myhost *\n",
            "uci show wireless": _WIRELESS_RAW,
        })
        with patch.object(adapter, "_ssh", return_value=_ssh_ctx(mock_client)):
            s = adapter.get_status()

        assert isinstance(s, AdapterStatus)
        assert s.model == "myrouter"
        assert s.uptime_seconds == 7200
        assert s.lan_ip == "192.168.1.1"
        assert s.wan_ip == "1.2.3.4"
        assert s.device_count == 1
        assert s.wifi_24_enabled is True
        assert s.wifi_5_enabled is False

    def test_graceful_on_empty_responses(self) -> None:
        adapter = _make_adapter()
        mock_client = _mock_ssh_client()  # all return b"" with exit 0
        with patch.object(adapter, "_ssh", return_value=_ssh_ctx(mock_client)):
            s = adapter.get_status()

        assert isinstance(s, AdapterStatus)
        assert s.uptime_seconds == 0
        assert s.device_count == 0


# ---------------------------------------------------------------------------
# get_devices
# ---------------------------------------------------------------------------

_ARP_RAW = (
    b"IP address       HW type     Flags       HW address            Mask     Device\n"
    b"192.168.1.10     0x1         0x2         aa:bb:cc:dd:ee:ff     *        br-lan\n"
    b"192.168.1.11     0x1         0x0         11:22:33:44:55:66     *        br-lan\n"
)
_LEASES_RAW = (
    b"1234567890 aa:bb:cc:dd:ee:ff 192.168.1.10 myhost *\n"
    b"1234567890 11:22:33:44:55:66 192.168.1.11 other *\n"
)


class TestGetDevices:
    def test_only_active_by_default(self) -> None:
        adapter = _make_adapter()
        mock_client = _mock_ssh_client({
            "cat /proc/net/arp": _ARP_RAW,
            "cat /tmp/dhcp.leases": _LEASES_RAW,
        })
        with patch.object(adapter, "_ssh", return_value=_ssh_ctx(mock_client)):
            devices = adapter.get_devices()

        assert len(devices) == 1
        assert devices[0].ip == "192.168.1.10"
        assert devices[0].active is True

    def test_all_devices(self) -> None:
        adapter = _make_adapter()
        mock_client = _mock_ssh_client({
            "cat /proc/net/arp": _ARP_RAW,
            "cat /tmp/dhcp.leases": _LEASES_RAW,
        })
        with patch.object(adapter, "_ssh", return_value=_ssh_ctx(mock_client)):
            devices = adapter.get_devices(only_active=False)

        assert len(devices) == 2
        inactive = next(d for d in devices if d.ip == "192.168.1.11")
        assert inactive.active is False

    def test_empty_leases(self) -> None:
        adapter = _make_adapter()
        mock_client = _mock_ssh_client()
        with patch.object(adapter, "_ssh", return_value=_ssh_ctx(mock_client)):
            devices = adapter.get_devices()
        assert devices == []


# ---------------------------------------------------------------------------
# get_wifi
# ---------------------------------------------------------------------------

_WIRELESS_UCI = (
    "wireless.radio0=wifi-device\n"
    "wireless.radio0.band='2g'\n"
    "wireless.radio0.channel='6'\n"
    "wireless.radio0.disabled='0'\n"
    "wireless.radio1=wifi-device\n"
    "wireless.radio1.hwmode='11a'\n"
    "wireless.radio1.channel='36'\n"
    "wireless.radio1.disabled='1'\n"
    "wireless.@wifi-iface[0]=wifi-iface\n"
    "wireless.@wifi-iface[0].device='radio0'\n"
    "wireless.@wifi-iface[0].ssid='MySSID'\n"
    "wireless.@wifi-iface[0].encryption='psk2'\n"
    "wireless.@wifi-iface[1]=wifi-iface\n"
    "wireless.@wifi-iface[1].device='radio1'\n"
    "wireless.@wifi-iface[1].ssid='MySSID_5G'\n"
    "wireless.@wifi-iface[1].encryption='psk2'\n"
)


class TestGetWifi:
    def test_parses_two_radios(self) -> None:
        adapter = _make_adapter()
        mock_client = _mock_ssh_client({"uci show wireless": _WIRELESS_UCI.encode()})
        with patch.object(adapter, "_ssh", return_value=_ssh_ctx(mock_client)):
            radios = adapter.get_wifi()

        assert len(radios) == 2
        r24 = next(r for r in radios if r.band == "2.4GHz")
        r5 = next(r for r in radios if r.band == "5GHz")
        assert r24.enabled is True
        assert r24.ssid == "MySSID"
        assert r24.channel == 6
        assert r5.enabled is False
        assert r5.ssid == "MySSID_5G"

    def test_returns_empty_on_error(self) -> None:
        adapter = _make_adapter()
        # uci command fails (non-zero exit)
        client = MagicMock()
        def exec_fail(cmd):
            stdout = MagicMock()
            stderr = MagicMock()
            stdout.channel.recv_exit_status.return_value = 1
            stdout.read.return_value = b""
            stderr.read.return_value = b"not found"
            return MagicMock(), stdout, stderr
        client.exec_command.side_effect = exec_fail
        with patch.object(adapter, "_ssh", return_value=_ssh_ctx(client)):
            radios = adapter.get_wifi()
        assert radios == []


# ---------------------------------------------------------------------------
# wifi_enable
# ---------------------------------------------------------------------------

class TestWifiEnable:
    def test_enable_sets_disabled_0(self) -> None:
        adapter = _make_adapter()
        mock_client = _mock_ssh_client({
            "uci show wireless": _WIRELESS_UCI.encode(),
        })
        with patch.object(adapter, "_ssh", return_value=_ssh_ctx(mock_client)):
            adapter.wifi_enable(True)

        cmds = [c.args[0] for c in mock_client.exec_command.call_args_list]
        assert any("disabled=0" in c for c in cmds)
        assert "uci commit wireless" in cmds
        assert "wifi" in cmds

    def test_disable_sets_disabled_1(self) -> None:
        adapter = _make_adapter()
        mock_client = _mock_ssh_client({
            "uci show wireless": _WIRELESS_UCI.encode(),
        })
        with patch.object(adapter, "_ssh", return_value=_ssh_ctx(mock_client)):
            adapter.wifi_enable(False)

        cmds = [c.args[0] for c in mock_client.exec_command.call_args_list]
        assert any("disabled=1" in c for c in cmds)

