"""Tests for the `plan` command and its helper functions."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from routerless.cli import _plan_dhcp, _plan_firewall, _plan_nat, cli
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
    TargetConfig,
    TargetType,
)


# ---------------------------------------------------------------------------
# _plan_dhcp
# ---------------------------------------------------------------------------

class TestPlanDhcp:
    _lease_a = StaticLease(name="NAS", mac="AA:BB:CC:DD:EE:FF", ip="192.168.1.20")
    _lease_b = StaticLease(name="Hub", mac="11:22:33:44:55:66", ip="192.168.1.10")

    def test_add_when_not_on_device(self) -> None:
        local = DHCPConfig(subnet="192.168.1.0/24", gateway="192.168.1.1", static_leases=[self._lease_a])
        items = _plan_dhcp(local, None)
        assert len(items) == 1
        action, desc = items[0]
        assert action == "add"
        assert "NAS" in desc
        assert "192.168.1.20" in desc

    def test_remove_when_absent_from_local(self) -> None:
        device = DHCPConfig(subnet="0.0.0.0/0", gateway="0.0.0.0", static_leases=[self._lease_b])
        items = _plan_dhcp(None, device)
        assert len(items) == 1
        action, desc = items[0]
        assert action == "remove"
        assert "Hub" in desc

    def test_change_when_ip_differs(self) -> None:
        local = DHCPConfig(
            subnet="192.168.1.0/24", gateway="192.168.1.1",
            static_leases=[StaticLease(name="NAS", mac="AA:BB:CC:DD:EE:FF", ip="192.168.1.99")],
        )
        device = DHCPConfig(
            subnet="0.0.0.0/0", gateway="0.0.0.0",
            static_leases=[self._lease_a],
        )
        items = _plan_dhcp(local, device)
        assert len(items) == 1
        action, desc = items[0]
        assert action == "change"
        assert "192.168.1.20 → 192.168.1.99" in desc

    def test_no_change_when_identical(self) -> None:
        local = DHCPConfig(subnet="192.168.1.0/24", gateway="192.168.1.1", static_leases=[self._lease_a])
        device = DHCPConfig(subnet="0.0.0.0/0", gateway="0.0.0.0", static_leases=[self._lease_a])
        items = _plan_dhcp(local, device)
        assert items == []

    def test_both_none(self) -> None:
        assert _plan_dhcp(None, None) == []

    def test_mac_comparison_case_insensitive(self) -> None:
        local_lease = StaticLease(name="NAS", mac="aa:bb:cc:dd:ee:ff", ip="192.168.1.20")
        device_lease = StaticLease(name="NAS", mac="AA:BB:CC:DD:EE:FF", ip="192.168.1.20")
        local = DHCPConfig(subnet="192.168.1.0/24", gateway="192.168.1.1", static_leases=[local_lease])
        device = DHCPConfig(subnet="0.0.0.0/0", gateway="0.0.0.0", static_leases=[device_lease])
        items = _plan_dhcp(local, device)
        assert items == []


# ---------------------------------------------------------------------------
# _plan_nat
# ---------------------------------------------------------------------------

class TestPlanNat:
    _pf_a = PortForward(name="Plex", external_port=32400, internal_ip="192.168.1.20", internal_port=32400)
    _pf_b = PortForward(name="HA",   external_port=8123,  internal_ip="192.168.1.30", internal_port=8123)

    def test_add_new_rule(self) -> None:
        local = NATConfig(port_forwards=[self._pf_a])
        items = _plan_nat(local, None)
        assert len(items) == 1
        action, desc = items[0]
        assert action == "add"
        assert "Plex" in desc
        assert "32400" in desc

    def test_remove_stale_rule(self) -> None:
        device = NATConfig(port_forwards=[self._pf_b])
        items = _plan_nat(None, device)
        assert len(items) == 1
        action, desc = items[0]
        assert action == "remove"
        assert "HA" in desc

    def test_change_dest_ip(self) -> None:
        modified = PortForward(name="Plex", external_port=32400, internal_ip="192.168.1.99", internal_port=32400)
        local = NATConfig(port_forwards=[modified])
        device = NATConfig(port_forwards=[self._pf_a])
        items = _plan_nat(local, device)
        assert len(items) == 1
        action, desc = items[0]
        assert action == "change"
        assert "192.168.1.20 → 192.168.1.99" in desc

    def test_no_change_when_identical(self) -> None:
        local = NATConfig(port_forwards=[self._pf_a])
        device = NATConfig(port_forwards=[self._pf_a])
        assert _plan_nat(local, device) == []

    def test_keyed_by_port_and_protocol(self) -> None:
        """Same port but different protocol → two separate adds."""
        pf_tcp = PortForward(name="TCP", external_port=80, internal_ip="192.168.1.1", internal_port=80, protocol=Protocol.TCP)
        pf_udp = PortForward(name="UDP", external_port=80, internal_ip="192.168.1.1", internal_port=80, protocol=Protocol.UDP)
        local = NATConfig(port_forwards=[pf_tcp])
        device = NATConfig(port_forwards=[pf_udp])
        items = _plan_nat(local, device)
        actions = [a for a, _ in items]
        assert "add" in actions
        assert "remove" in actions


# ---------------------------------------------------------------------------
# _plan_firewall
# ---------------------------------------------------------------------------

class TestPlanFirewall:
    _rule_a = FirewallRule(name="Block IoT", direction=FirewallDirection.FORWARD, action=FirewallAction.DROP)
    _rule_b = FirewallRule(name="Allow SSH", direction=FirewallDirection.INPUT,   action=FirewallAction.ACCEPT)

    def test_add_new_rule(self) -> None:
        local = FirewallConfig(rules=[self._rule_a])
        items = _plan_firewall(local, None)
        assert len(items) == 1
        action, desc = items[0]
        assert action == "add"
        assert "Block IoT" in desc

    def test_remove_stale_rule(self) -> None:
        device = FirewallConfig(rules=[self._rule_b])
        items = _plan_firewall(None, device)
        assert len(items) == 1
        action, _ = items[0]
        assert action == "remove"

    def test_change_action(self) -> None:
        modified = FirewallRule(name="Block IoT", direction=FirewallDirection.FORWARD, action=FirewallAction.REJECT)
        local = FirewallConfig(rules=[modified])
        device = FirewallConfig(rules=[self._rule_a])
        items = _plan_firewall(local, device)
        assert len(items) == 1
        action, desc = items[0]
        assert action == "change"
        assert "DROP" in desc and "REJECT" in desc

    def test_no_change_when_identical(self) -> None:
        local = FirewallConfig(rules=[self._rule_a])
        device = FirewallConfig(rules=[self._rule_a])
        assert _plan_firewall(local, device) == []


# ---------------------------------------------------------------------------
# cmd_plan  (via CliRunner)
# ---------------------------------------------------------------------------

TARGET_CFG = TargetConfig(
    type=TargetType.OPENWRT,
    host="192.168.1.1",
    ssh_user="root",
    ssh_password="pass",
    ssh_port=22,
)

_LEASE = StaticLease(name="NAS", mac="AA:BB:CC:DD:EE:FF", ip="192.168.1.20")
_PF    = PortForward(name="Plex", external_port=32400, internal_ip="192.168.1.20", internal_port=32400)


def _build_network_cfg(leases=(), pfs=()) -> NetworkConfig:
    from routerless.models.config import DHCPConfig, NATConfig
    return NetworkConfig(
        targets={"myrouter": TARGET_CFG},
        dhcp=DHCPConfig(subnet="192.168.1.0/24", gateway="192.168.1.1", static_leases=list(leases)) if leases else None,
        nat=NATConfig(port_forwards=list(pfs)) if pfs else None,
    )


class TestCmdPlan:
    def _run(self, local_cfg: NetworkConfig, device_cfg: NetworkConfig, sections: list[str] | None = None) -> str:
        runner = CliRunner()
        adapter_mock = MagicMock()
        adapter_mock.dump.return_value = device_cfg
        with runner.isolated_filesystem():
            # create a dummy file so click.Path(exists=True) passes
            with open("config.yaml", "w") as f:
                f.write("version: '1.0'\ntargets: {}\n")
            with (
                patch("routerless.cli._load", return_value=local_cfg),
                patch("routerless.cli._get_adapter", return_value=adapter_mock),
            ):
                args = ["plan", "--target", "myrouter", "config.yaml"]
                for s in (sections or []):
                    args += ["--section", s]
                result = runner.invoke(cli, args)
        return result.output

    def test_no_changes_message(self) -> None:
        cfg = _build_network_cfg(leases=[_LEASE])
        out = self._run(local_cfg=cfg, device_cfg=cfg)
        assert "no changes" in out

    def test_shows_add(self) -> None:
        local = _build_network_cfg(leases=[_LEASE])
        device = _build_network_cfg()
        out = self._run(local_cfg=local, device_cfg=device)
        assert "ADD" in out
        assert "NAS" in out

    def test_shows_remove(self) -> None:
        local = _build_network_cfg(leases=[])  # dhcp section present but empty
        # give device a lease that local doesn't have
        from routerless.models.config import DHCPConfig
        device_cfg = NetworkConfig(
            targets={"myrouter": TARGET_CFG},
            dhcp=DHCPConfig(subnet="0.0.0.0/0", gateway="0.0.0.0", static_leases=[_LEASE]),
        )
        local_cfg = NetworkConfig(
            targets={"myrouter": TARGET_CFG},
            dhcp=DHCPConfig(subnet="192.168.1.0/24", gateway="192.168.1.1", static_leases=[]),
        )
        out = self._run(local_cfg=local_cfg, device_cfg=device_cfg)
        assert "REMOVE" in out
        assert "NAS" in out

    def test_shows_change(self) -> None:
        modified = StaticLease(name="NAS", mac="AA:BB:CC:DD:EE:FF", ip="192.168.1.99")
        local = _build_network_cfg(leases=[modified])
        device = _build_network_cfg(leases=[_LEASE])
        out = self._run(local_cfg=local, device_cfg=device)
        assert "CHANGE" in out
        assert "192.168.1.20 → 192.168.1.99" in out

    def test_plan_summary_counts(self) -> None:
        pf_new = PortForward(name="New", external_port=9000, internal_ip="192.168.1.5", internal_port=9000)
        local = _build_network_cfg(leases=[_LEASE], pfs=[_PF, pf_new])
        device = _build_network_cfg(pfs=[_PF])
        out = self._run(local_cfg=local, device_cfg=device)
        # 1 lease add (dhcp) + 1 nat rule add = 2 to add
        assert "2 to add" in out

    def test_section_filter(self) -> None:
        """With --section dhcp only, nat section must not appear."""
        local = _build_network_cfg(leases=[_LEASE], pfs=[_PF])
        device = _build_network_cfg()
        out = self._run(local_cfg=local, device_cfg=device, sections=["dhcp"])
        assert "Section: dhcp" in out
        assert "Section: nat" not in out

    def test_shows_apply_command_when_changes(self) -> None:
        local = _build_network_cfg(leases=[_LEASE])
        device = _build_network_cfg()
        out = self._run(local_cfg=local, device_cfg=device)
        assert "routerless apply" in out

    def test_dump_error_raises_clickexception(self) -> None:
        runner = CliRunner()
        cfg = _build_network_cfg(leases=[_LEASE])
        adapter_mock = MagicMock()
        adapter_mock.dump.side_effect = RuntimeError("SSH timeout")
        with runner.isolated_filesystem():
            with open("config.yaml", "w") as f:
                f.write("version: '1.0'\ntargets: {}\n")
            with (
                patch("routerless.cli._load", return_value=cfg),
                patch("routerless.cli._get_adapter", return_value=adapter_mock),
            ):
                result = runner.invoke(cli, ["plan", "--target", "myrouter", "config.yaml"])
        assert result.exit_code != 0
        assert "SSH timeout" in result.output
