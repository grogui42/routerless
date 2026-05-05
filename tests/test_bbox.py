"""Tests for the Bbox Ultim adapter — HTTP is mocked via httpx."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from routerless.adapters.bbox_ultim import BboxUltimAdapter, BboxDevice, BboxStatus, BboxWifiRadio
from routerless.models.config import (
    DHCPConfig,
    NATConfig,
    PortForward,
    Protocol,
    StaticLease,
    TargetConfig,
    TargetType,
)

TARGET = TargetConfig(
    type=TargetType.BBOX_ULTIM,
    host="192.168.1.254",
    password="secret",
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _adapter() -> BboxUltimAdapter:
    return BboxUltimAdapter(TARGET)


def _mock_http(adapter: BboxUltimAdapter, mock_client: MagicMock):
    """Return a stack of patches that intercept _make_client, _login, _logout."""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=mock_client)
    cm.__exit__ = MagicMock(return_value=False)
    p_make = patch.object(adapter, "_make_client", return_value=cm)
    p_login = patch.object(adapter, "_login", return_value=None)
    p_logout = patch.object(adapter, "_logout")
    return p_make, p_login, p_logout


def _bbox_resp(data: Any, status: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = data
    r.raise_for_status = MagicMock()
    return r


# ---------------------------------------------------------------------------
# _extract_list
# ---------------------------------------------------------------------------

class TestExtractList:
    def test_dhcp_clients(self) -> None:
        data = [{"dhcp": {"clients": {"list": [{"id": 1, "macaddress": "AA:BB:CC:DD:EE:FF"}], "number": 1}}}]
        result = BboxUltimAdapter._extract_list(data, "dhcp", "clients")
        assert len(result) == 1
        assert result[0]["id"] == 1

    def test_nat_rules(self) -> None:
        data = [{"nat": {"rules": {"list": [{"id": 5, "description": "HA"}], "number": 1}}}]
        result = BboxUltimAdapter._extract_list(data, "nat", "rules")
        assert result[0]["id"] == 5

    def test_empty_list(self) -> None:
        data = [{"dhcp": {"clients": {"list": [], "number": 0}}}]
        assert BboxUltimAdapter._extract_list(data, "dhcp", "clients") == []

    def test_malformed_returns_empty(self) -> None:
        assert BboxUltimAdapter._extract_list([], "dhcp") == []
        assert BboxUltimAdapter._extract_list(None, "dhcp") == []  # type: ignore[arg-type]
        assert BboxUltimAdapter._extract_list([{"other": {}}], "dhcp", "clients") == []


# ---------------------------------------------------------------------------
# apply_dhcp
# ---------------------------------------------------------------------------

class TestApplyDhcp:
    def _dhcp_response(self, leases: list[dict]) -> Any:
        return [{"dhcp": {"clients": {"list": leases, "number": len(leases)}}}]

    def test_creates_new_lease(self) -> None:
        adapter = _adapter()
        config = DHCPConfig(
            subnet="192.168.1.0/24",
            gateway="192.168.1.1",
            static_leases=[StaticLease(name="Hub", mac="AA:BB:CC:DD:EE:FF", ip="192.168.1.10")],
        )
        mock_client = MagicMock()
        mock_client.get.return_value = _bbox_resp(self._dhcp_response([]))
        mock_client.post.return_value = _bbox_resp({})
        mock_client.delete.return_value = _bbox_resp({})

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            adapter.apply_dhcp(config)

        # Should have called _get (via _list_dhcp_clients) then _post (create)
        mock_client.get.assert_called_once()
        assert "dhcp/clients" in mock_client.get.call_args[0][0]
        mock_client.post.assert_called_once()
        post_data = mock_client.post.call_args[1]["data"]
        assert post_data["macaddress"] == "AA:BB:CC:DD:EE:FF"
        assert post_data["ipaddress"] == "192.168.1.10"

    def test_skips_identical_lease(self) -> None:
        adapter = _adapter()
        config = DHCPConfig(
            subnet="192.168.1.0/24",
            gateway="192.168.1.1",
            static_leases=[StaticLease(name="Hub", mac="AA:BB:CC:DD:EE:FF", ip="192.168.1.10", hostname="Hub")],
        )
        existing = [{"id": 1, "macaddress": "AA:BB:CC:DD:EE:FF", "ipaddress": "192.168.1.10", "hostname": "Hub"}]
        mock_client = MagicMock()
        mock_client.get.return_value = _bbox_resp(self._dhcp_response(existing))

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            adapter.apply_dhcp(config)

        mock_client.post.assert_not_called()
        mock_client.delete.assert_not_called()

    def test_updates_changed_ip(self) -> None:
        adapter = _adapter()
        config = DHCPConfig(
            subnet="192.168.1.0/24",
            gateway="192.168.1.1",
            static_leases=[StaticLease(name="Hub", mac="AA:BB:CC:DD:EE:FF", ip="192.168.1.99", hostname="Hub")],
        )
        existing = [{"id": 7, "macaddress": "AA:BB:CC:DD:EE:FF", "ipaddress": "192.168.1.10", "hostname": "Hub"}]
        mock_client = MagicMock()
        mock_client.get.return_value = _bbox_resp(self._dhcp_response(existing))
        mock_client.delete.return_value = _bbox_resp({})
        mock_client.post.return_value = _bbox_resp({})

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            adapter.apply_dhcp(config)

        # Should delete old then create new
        mock_client.delete.assert_called_once()
        assert "/7" in mock_client.delete.call_args[0][0]
        mock_client.post.assert_called_once()

    def test_removes_stale_lease(self) -> None:
        adapter = _adapter()
        config = DHCPConfig(
            subnet="192.168.1.0/24",
            gateway="192.168.1.1",
            static_leases=[],  # no leases desired
        )
        existing = [{"id": 3, "macaddress": "AA:BB:CC:DD:EE:FF", "ipaddress": "192.168.1.10", "hostname": "Old"}]
        mock_client = MagicMock()
        mock_client.get.return_value = _bbox_resp(self._dhcp_response(existing))
        mock_client.delete.return_value = _bbox_resp({})

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            adapter.apply_dhcp(config)

        mock_client.delete.assert_called_once()
        assert "/3" in mock_client.delete.call_args[0][0]


# ---------------------------------------------------------------------------
# apply_nat
# ---------------------------------------------------------------------------

class TestApplyNat:
    def _nat_response(self, rules: list[dict]) -> Any:
        return [{"nat": {"rules": {"list": rules, "number": len(rules)}}}]

    def test_creates_new_rule(self) -> None:
        adapter = _adapter()
        config = NATConfig(port_forwards=[
            PortForward(name="HA", protocol=Protocol.TCP, external_port=8123,
                        internal_ip="192.168.1.20", internal_port=8123),
        ])
        mock_client = MagicMock()
        mock_client.get.return_value = _bbox_resp(self._nat_response([]))
        mock_client.post.return_value = _bbox_resp({})

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            adapter.apply_nat(config)

        mock_client.post.assert_called_once()
        data = mock_client.post.call_args[1]["data"]
        assert data["ipprotocol"] == "tcp"
        assert data["external_port"] == "8123"
        assert data["ipaddress"] == "192.168.1.20"

    def test_skips_identical_rule(self) -> None:
        adapter = _adapter()
        config = NATConfig(port_forwards=[
            PortForward(name="HA", protocol=Protocol.TCP, external_port=8123,
                        internal_ip="192.168.1.20", internal_port=8123),
        ])
        existing = [{"id": 1, "description": "HA", "ipprotocol": "tcp",
                     "external_port": "8123", "ipaddress": "192.168.1.20", "internal_port": "8123"}]
        mock_client = MagicMock()
        mock_client.get.return_value = _bbox_resp(self._nat_response(existing))

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            adapter.apply_nat(config)

        mock_client.post.assert_not_called()
        mock_client.delete.assert_not_called()

    def test_both_protocol_maps_to_tcpudp(self) -> None:
        adapter = _adapter()
        config = NATConfig(port_forwards=[
            PortForward(name="DNS", protocol=Protocol.BOTH, external_port=53,
                        internal_ip="192.168.1.5", internal_port=53),
        ])
        mock_client = MagicMock()
        mock_client.get.return_value = _bbox_resp(self._nat_response([]))
        mock_client.post.return_value = _bbox_resp({})

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            adapter.apply_nat(config)

        data = mock_client.post.call_args[1]["data"]
        assert data["ipprotocol"] == "tcpudp"

    def test_removes_stale_rule(self) -> None:
        adapter = _adapter()
        config = NATConfig(port_forwards=[])  # empty desired
        existing = [{"id": 9, "description": "Old", "ipprotocol": "tcp",
                     "external_port": "80", "ipaddress": "192.168.1.1", "internal_port": "80"}]
        mock_client = MagicMock()
        mock_client.get.return_value = _bbox_resp(self._nat_response(existing))
        mock_client.delete.return_value = _bbox_resp({})

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            adapter.apply_nat(config)

        mock_client.delete.assert_called_once()
        assert "/9" in mock_client.delete.call_args[0][0]


# ---------------------------------------------------------------------------
# apply_firewall — must raise NotImplementedError
# ---------------------------------------------------------------------------

class TestApplyFirewall:
    def test_raises_not_implemented(self) -> None:
        from routerless.models.config import FirewallConfig
        adapter = _adapter()
        with pytest.raises(NotImplementedError):
            adapter.apply_firewall(FirewallConfig())


# ---------------------------------------------------------------------------
# dump
# ---------------------------------------------------------------------------

class TestDump:
    def test_dump_returns_leases_and_nat(self) -> None:
        adapter = _adapter()
        dhcp_resp = [{"dhcp": {"clients": {"list": [
            {"id": 1, "macaddress": "AA:BB:CC:DD:EE:FF",
             "ipaddress": "192.168.1.10", "hostname": "hub"}
        ], "number": 1}}}]
        nat_resp = [{"nat": {"rules": {"list": [
            {"id": 2, "description": "HA", "ipprotocol": "tcp",
             "external_port": "8123", "ipaddress": "192.168.1.20", "internal_port": "8123"}
        ], "number": 1}}}]
        hosts_resp: list = []

        mock_client = MagicMock()
        # dump() calls: GET /hosts, then _list_dhcp_clients (GET /dhcp/clients),
        # then _list_nat_rules (GET /nat/rules)
        mock_client.get.side_effect = [
            _bbox_resp(hosts_resp),   # /hosts
            _bbox_resp(dhcp_resp),    # /dhcp/clients (via _list_dhcp_clients)
            _bbox_resp(nat_resp),     # /nat/rules (via _list_nat_rules)
        ]

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            cfg = adapter.dump()

        assert cfg.dhcp is not None
        assert len(cfg.dhcp.static_leases) == 1
        assert cfg.dhcp.static_leases[0].mac == "AA:BB:CC:DD:EE:FF"
        assert cfg.nat is not None
        assert len(cfg.nat.port_forwards) == 1
        assert cfg.nat.port_forwards[0].external_port == 8123
        assert cfg.firewall is None


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------

class TestGetStatus:
    def _make_responses(self) -> list[MagicMock]:
        """Returns side_effect list for 6 sequential GET calls in get_status()."""
        wan = [{"wan": {"ip": {"address": "90.1.2.3"}, "internet": {"state": "Connected"}}}]
        lan = [{"lan": {"ip": {"address": "192.168.1.254"}}}]
        device = [{"device": {"uptime": 3661, "modelname": "Bbox Ultim", "serialnumber": "SN123"}}]
        wireless = [{"wireless": {"radio": {"24": {"enable": 1}, "5": {"enable": 0}}}}]
        voip = [{"voip": [{"status": "Up"}]}]
        hosts = [{"hosts": {"list": [{"ipaddress": "192.168.1.10"}, {"ipaddress": "192.168.1.11"}]}}]
        return [_bbox_resp(d) for d in (wan, lan, device, wireless, voip, hosts)]

    def test_parses_status(self) -> None:
        adapter = _adapter()
        mock_client = MagicMock()
        mock_client.get.side_effect = self._make_responses()

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            s = adapter.get_status()

        assert s.wan_ip == "90.1.2.3"
        assert s.lan_ip == "192.168.1.254"
        assert s.internet_state == "Connected"
        assert s.voip_status == "Up"
        assert s.wifi_24_enabled is True
        assert s.wifi_5_enabled is False
        assert s.device_count == 2
        assert s.uptime_seconds == 3661
        assert s.model == "Bbox Ultim"
        assert s.serial == "SN123"

    def test_no_5ghz_radio(self) -> None:
        adapter = _adapter()
        mock_client = MagicMock()
        wan = [{"wan": {"ip": {"address": "1.2.3.4"}, "internet": {"state": "OK"}}}]
        lan = [{"lan": {"ip": {"address": "192.168.1.254"}}}]
        device = [{"device": {"uptime": 0, "modelname": "", "serialnumber": ""}}]
        wireless = [{"wireless": {"radio": {"24": {"enable": 1}}}}]  # no 5GHz key
        voip = [{"voip": [{"status": "Down"}]}]
        hosts = [{"hosts": {"list": []}}]
        mock_client.get.side_effect = [_bbox_resp(d) for d in (wan, lan, device, wireless, voip, hosts)]

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            s = adapter.get_status()

        assert s.wifi_5_enabled is None


# ---------------------------------------------------------------------------
# get_devices
# ---------------------------------------------------------------------------

class TestGetDevices:
    def _hosts_response(self, hosts: list[dict]) -> Any:
        return [{"hosts": {"list": hosts, "listcount": len(hosts)}}]

    def test_active_only_by_default(self) -> None:
        adapter = _adapter()
        data = self._hosts_response([
            {"ipaddress": "192.168.1.10", "macaddress": "AA:BB:CC:DD:EE:01",
             "hostname": "pc1", "devicetype": "", "link": "Ethernet",
             "ethernet": {"logicalport": 1}, "active": 1},
            {"ipaddress": "192.168.1.20", "macaddress": "AA:BB:CC:DD:EE:02",
             "hostname": "phone", "devicetype": "", "link": "Wifi",
             "wireless": {"rssi0": -65, "band": "5"}, "active": 0},
        ])
        mock_client = MagicMock()
        mock_client.get.return_value = _bbox_resp(data)

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            devices = adapter.get_devices(only_active=True)

        assert len(devices) == 1
        assert devices[0].hostname == "pc1"
        assert devices[0].link == "Ethernet port 1"

    def test_all_devices(self) -> None:
        adapter = _adapter()
        data = self._hosts_response([
            {"ipaddress": "192.168.1.10", "macaddress": "AA:BB:CC:DD:EE:01",
             "hostname": "pc1", "devicetype": "", "link": "Ethernet",
             "ethernet": {"logicalport": 2}, "active": 1},
            {"ipaddress": "192.168.1.20", "macaddress": "AA:BB:CC:DD:EE:02",
             "hostname": "tv", "devicetype": "STB", "link": "Ethernet",
             "ethernet": {"logicalport": 1}, "active": 0},
        ])
        mock_client = MagicMock()
        mock_client.get.return_value = _bbox_resp(data)

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            devices = adapter.get_devices(only_active=False)

        assert len(devices) == 2
        assert devices[1].device_type == "STB"
        assert devices[1].active is False

    def test_wifi_link_format(self) -> None:
        adapter = _adapter()
        # aiobbox: link field is already "Wifi 2.4", "Wifi 5", etc. (band included)
        data = self._hosts_response([
            {"ipaddress": "192.168.1.30", "macaddress": "AA:BB:CC:DD:EE:03",
             "hostname": "laptop", "devicetype": "", "link": "Wifi 2.4",
             "wireless": {"rssi0": -72, "band": 2.4}, "active": 1},
        ])
        mock_client = MagicMock()
        mock_client.get.return_value = _bbox_resp(data)

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            devices = adapter.get_devices()

        assert devices[0].link == "Wifi 2.4 RSSI -72"


# ---------------------------------------------------------------------------
# get_wifi / wifi_enable
# ---------------------------------------------------------------------------

class TestGetWifi:
    def test_two_bands(self) -> None:
        adapter = _adapter()
        wifi_data = [{"wireless": {
            "radio": {
                "24": {"enable": 1, "current_channel": 6},
                "5": {"enable": 1, "current_channel": 36},
            },
            "ssid": {
                "24": {"id": "Home-2.4", "security": {"protocol": "WPA+WPA2", "encryption": "AES"}},
                "5": {"id": "Home-5", "security": {"protocol": "WPA+WPA2", "encryption": "AES"}},
            },
        }}]
        hosts_data = [{"hosts": {"list": [{"active": 1}, {"active": 1}]}}]
        mock_client = MagicMock()
        mock_client.get.side_effect = [_bbox_resp(wifi_data), _bbox_resp(hosts_data)]

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            radios = adapter.get_wifi()

        assert len(radios) == 2
        assert radios[0].band == "2.4GHz"
        assert radios[0].ssid == "Home-2.4"
        assert radios[0].channel == 6
        assert radios[0].device_count == 2
        assert radios[1].band == "5GHz"
        assert radios[1].ssid == "Home-5"
        assert radios[1].device_count is None

    def test_only_24ghz(self) -> None:
        adapter = _adapter()
        wifi_data = [{"wireless": {
            "radio": {"24": {"enable": 1, "current_channel": 11}},
            "ssid": {"24": {"id": "Home", "security": {"protocol": "WPA2", "encryption": "AES"}}},
        }}]
        hosts_data = [{"hosts": {"list": []}}]
        mock_client = MagicMock()
        mock_client.get.side_effect = [_bbox_resp(wifi_data), _bbox_resp(hosts_data)]

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            radios = adapter.get_wifi()

        assert len(radios) == 1
        assert radios[0].band == "2.4GHz"


class TestWifiEnable:
    def test_wifi_on_sends_put(self) -> None:
        adapter = _adapter()
        mock_client = MagicMock()
        mock_client.put.return_value = _bbox_resp({})

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            adapter.wifi_enable(True)

        mock_client.put.assert_called_once()
        url, = mock_client.put.call_args[0]
        assert "wireless" in url
        assert mock_client.put.call_args[1]["data"]["radio.enable"] == "1"

    def test_wifi_off_sends_put(self) -> None:
        adapter = _adapter()
        mock_client = MagicMock()
        mock_client.put.return_value = _bbox_resp({})

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            adapter.wifi_enable(False)

        assert mock_client.put.call_args[1]["data"]["radio.enable"] == "0"
