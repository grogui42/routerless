"""Tests for Pydantic config models."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from routerless.models.config import (
    DHCPConfig,
    FirewallAction,
    FirewallConfig,
    FirewallRule,
    NATConfig,
    NetworkConfig,
    PortForward,
    Protocol,
    StaticLease,
    TargetConfig,
    TargetType,
    parse_config,
)


class TestStaticLease:
    def test_valid(self) -> None:
        l = StaticLease(name="Hub", mac="aa:bb:cc:dd:ee:ff", ip="192.168.1.10")
        assert l.mac == "AA:BB:CC:DD:EE:FF"

    def test_mac_normalised_dashes(self) -> None:
        l = StaticLease(name="X", mac="aa-bb-cc-dd-ee-ff", ip="10.0.0.1")
        assert l.mac == "AA:BB:CC:DD:EE:FF"

    def test_invalid_mac(self) -> None:
        with pytest.raises(ValidationError, match="MAC"):
            StaticLease(name="X", mac="not-a-mac", ip="10.0.0.1")

    def test_invalid_ip(self) -> None:
        with pytest.raises(ValidationError):
            StaticLease(name="X", mac="aa:bb:cc:dd:ee:ff", ip="999.0.0.1")


class TestDHCPConfig:
    def test_valid(self) -> None:
        c = DHCPConfig(subnet="192.168.1.0/24", gateway="192.168.1.1")
        assert c.lease_time == "24h"

    def test_invalid_subnet(self) -> None:
        with pytest.raises(ValidationError):
            DHCPConfig(subnet="not-a-subnet", gateway="192.168.1.1")

    def test_invalid_gateway(self) -> None:
        with pytest.raises(ValidationError):
            DHCPConfig(subnet="192.168.1.0/24", gateway="notanip")


class TestPortForward:
    def test_valid(self) -> None:
        p = PortForward(
            name="HA", protocol="tcp", external_port=8123,
            internal_ip="192.168.1.20", internal_port=8123,
        )
        assert p.protocol == Protocol.TCP

    def test_port_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            PortForward(name="X", external_port=70000, internal_ip="10.0.0.1", internal_port=80)


class TestTargetConfig:
    def test_bbox_valid(self) -> None:
        t = TargetConfig(type="bbox_ultim", host="192.168.1.254", password="pass")
        assert t.type == TargetType.BBOX_ULTIM

    def test_bbox_missing_password(self) -> None:
        with pytest.raises(ValidationError, match="password"):
            TargetConfig(type="bbox_ultim", host="192.168.1.254")

    def test_openwrt_valid_with_key(self) -> None:
        t = TargetConfig(
            type="openwrt", host="192.168.1.1",
            ssh_user="root", ssh_key="/home/user/.ssh/id_rsa",
        )
        assert t.ssh_port == 22

    def test_openwrt_missing_credentials(self) -> None:
        with pytest.raises(ValidationError):
            TargetConfig(type="openwrt", host="192.168.1.1", ssh_user="root")


class TestParseConfig:
    def test_full_config(self) -> None:
        raw = {
            "version": "1.0",
            "targets": {
                "home": {
                    "type": "openwrt",
                    "host": "192.168.1.1",
                    "ssh_user": "root",
                    "ssh_password": "secret",
                }
            },
            "dhcp": {
                "subnet": "192.168.1.0/24",
                "gateway": "192.168.1.1",
                "static_leases": [
                    {"name": "Hub", "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.1.10"}
                ],
            },
            "nat": {
                "port_forwards": [
                    {"name": "HA", "external_port": 8123, "internal_ip": "192.168.1.20", "internal_port": 8123}
                ]
            },
            "firewall": {
                "rules": [
                    {"name": "Block IoT", "direction": "forward", "src": "iot", "dest": "wan", "action": "DROP"}
                ]
            },
        }
        cfg = parse_config(raw)
        assert len(cfg.dhcp.static_leases) == 1
        assert len(cfg.nat.port_forwards) == 1
        assert len(cfg.firewall.rules) == 1

    def test_empty_config(self) -> None:
        cfg = parse_config({})
        assert cfg.dhcp is None
        assert cfg.nat is None
        assert cfg.firewall is None
