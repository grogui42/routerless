"""Tests for the `import` command and _merge_section helper."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from click.testing import CliRunner

from routerless.cli import _merge_section, cli
from routerless.models.config import (
    DHCPConfig,
    FirewallConfig,
    FirewallRule,
    NATConfig,
    NetworkConfig,
    PortForward,
    StaticLease,
    TargetConfig,
    TargetType,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TARGET_CFG = TargetConfig(
    type=TargetType.OPENWRT,
    host="192.168.1.1",
    ssh_user="root",
    ssh_password="pass",
)

_LEASE_A = StaticLease(name="NAS", mac="AA:BB:CC:DD:EE:FF", ip="192.168.1.20")
_LEASE_B = StaticLease(name="Hub", mac="11:22:33:44:55:66", ip="192.168.1.10")
_PF_A = PortForward(name="Plex", external_port=32400, internal_ip="192.168.1.20", internal_port=32400)
_RULE_A = FirewallRule(name="Block IoT", src="iot", dest="wan")


def _device_cfg(
    leases: list[StaticLease] | None = None,
    pfs: list[PortForward] | None = None,
    rules: list[FirewallRule] | None = None,
) -> NetworkConfig:
    return NetworkConfig(
        targets={"router": TARGET_CFG},
        dhcp=(
            DHCPConfig(subnet="192.168.1.0/24", gateway="192.168.1.1", static_leases=leases or [])
            if leases is not None else None
        ),
        nat=NATConfig(port_forwards=pfs or []) if pfs is not None else None,
        firewall=FirewallConfig(rules=rules or []) if rules is not None else None,
    )


def _run_import(device_cfg: NetworkConfig, args: list[str], input: str = "") -> tuple[int, str]:
    runner = CliRunner()
    adapter_mock = MagicMock()
    adapter_mock.dump.return_value = device_cfg
    with runner.isolated_filesystem():
        Path("config.yaml").write_text("version: '1.0'\ntargets: {}\n")
        with (
            patch("routerless.cli._load", return_value=NetworkConfig(targets={"router": TARGET_CFG})),
            patch("routerless.cli._get_adapter", return_value=adapter_mock),
        ):
            result = runner.invoke(cli, ["import", "--target", "router", "config.yaml"] + args, input=input)
            # Collect output files from isolated filesystem
            files = {p.name: p.read_text() for p in Path(".").glob("*.yaml") if p.name != "config.yaml"}
    return result.exit_code, result.output, files


# ---------------------------------------------------------------------------
# _merge_section
# ---------------------------------------------------------------------------

class TestMergeSection:
    def test_dhcp_appends_new_leases(self) -> None:
        existing = {"static_leases": [{"name": "NAS", "mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.1.20"}]}
        device = {"static_leases": [
            {"name": "NAS", "mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.1.20"},
            {"name": "Hub", "mac": "11:22:33:44:55:66", "ip": "192.168.1.10"},
        ]}
        result = _merge_section("dhcp", existing, device)
        assert len(result["static_leases"]) == 2
        assert result["static_leases"][1]["name"] == "Hub"

    def test_dhcp_skips_existing_mac(self) -> None:
        existing = {"static_leases": [{"name": "NAS", "mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.1.20"}]}
        device = {"static_leases": [{"name": "NAS", "mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.1.20"}]}
        result = _merge_section("dhcp", existing, device)
        assert len(result["static_leases"]) == 1

    def test_dhcp_mac_comparison_case_insensitive(self) -> None:
        existing = {"static_leases": [{"name": "NAS", "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.1.20"}]}
        device = {"static_leases": [{"name": "NAS", "mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.1.20"}]}
        result = _merge_section("dhcp", existing, device)
        assert len(result["static_leases"]) == 1

    def test_nat_appends_new_port_forwards(self) -> None:
        existing = {"port_forwards": [{"name": "Plex", "external_port": 32400, "protocol": "tcp"}]}
        device = {"port_forwards": [
            {"name": "Plex", "external_port": 32400, "protocol": "tcp"},
            {"name": "HA", "external_port": 8123, "protocol": "tcp"},
        ]}
        result = _merge_section("nat", existing, device)
        assert len(result["port_forwards"]) == 2
        assert result["port_forwards"][1]["name"] == "HA"

    def test_nat_skips_existing_port_proto(self) -> None:
        existing = {"port_forwards": [{"name": "Plex", "external_port": 32400, "protocol": "tcp"}]}
        device = {"port_forwards": [{"name": "Plex", "external_port": 32400, "protocol": "tcp"}]}
        result = _merge_section("nat", existing, device)
        assert len(result["port_forwards"]) == 1

    def test_firewall_appends_new_rules(self) -> None:
        existing = {"rules": [{"name": "Block IoT"}]}
        device = {"rules": [{"name": "Block IoT"}, {"name": "Allow LAN"}]}
        result = _merge_section("firewall", existing, device)
        assert len(result["rules"]) == 2
        assert result["rules"][1]["name"] == "Allow LAN"

    def test_firewall_skips_existing_name(self) -> None:
        existing = {"rules": [{"name": "Block IoT"}]}
        device = {"rules": [{"name": "Block IoT"}]}
        result = _merge_section("firewall", existing, device)
        assert len(result["rules"]) == 1

    def test_empty_existing(self) -> None:
        device = {"static_leases": [{"name": "NAS", "mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.1.20"}]}
        result = _merge_section("dhcp", {}, device)
        assert len(result["static_leases"]) == 1


# ---------------------------------------------------------------------------
# cmd_import — file creation
# ---------------------------------------------------------------------------

class TestCmdImportCreate:
    def test_creates_section_files_when_absent(self) -> None:
        device = _device_cfg(leases=[_LEASE_A], pfs=[_PF_A], rules=[_RULE_A])
        runner = CliRunner()
        adapter_mock = MagicMock()
        adapter_mock.dump.return_value = device
        with runner.isolated_filesystem():
            Path("config.yaml").write_text("version: '1.0'\ntargets: {}\n")
            with (
                patch("routerless.cli._load", return_value=NetworkConfig(targets={"r": TARGET_CFG})),
                patch("routerless.cli._get_adapter", return_value=adapter_mock),
            ):
                result = runner.invoke(cli, ["import", "--target", "r", "config.yaml"])
            assert result.exit_code == 0, result.output
            assert Path("dhcp.yaml").exists()
            assert Path("nat.yaml").exists()
            assert Path("firewall.yaml").exists()
            dhcp = yaml.safe_load(Path("dhcp.yaml").read_text())
            assert dhcp["static_leases"][0]["mac"] == "AA:BB:CC:DD:EE:FF"

    def test_section_filter_only_writes_requested(self) -> None:
        device = _device_cfg(leases=[_LEASE_A], pfs=[_PF_A])
        runner = CliRunner()
        adapter_mock = MagicMock()
        adapter_mock.dump.return_value = device
        with runner.isolated_filesystem():
            Path("config.yaml").write_text("version: '1.0'\ntargets: {}\n")
            with (
                patch("routerless.cli._load", return_value=NetworkConfig(targets={"r": TARGET_CFG})),
                patch("routerless.cli._get_adapter", return_value=adapter_mock),
            ):
                result = runner.invoke(cli, ["import", "--target", "r", "--section", "nat", "config.yaml"])
            assert result.exit_code == 0, result.output
            assert Path("nat.yaml").exists()
            assert not Path("dhcp.yaml").exists()

    def test_output_dir_option(self) -> None:
        device = _device_cfg(leases=[_LEASE_A])
        runner = CliRunner()
        adapter_mock = MagicMock()
        adapter_mock.dump.return_value = device
        with runner.isolated_filesystem():
            Path("config.yaml").write_text("version: '1.0'\ntargets: {}\n")
            with (
                patch("routerless.cli._load", return_value=NetworkConfig(targets={"r": TARGET_CFG})),
                patch("routerless.cli._get_adapter", return_value=adapter_mock),
            ):
                result = runner.invoke(
                    cli, ["import", "--target", "r", "--section", "dhcp", "--output-dir", "out", "config.yaml"]
                )
            assert result.exit_code == 0, result.output
            assert Path("out/dhcp.yaml").exists()

    def test_skips_section_with_no_device_data(self) -> None:
        device = _device_cfg()  # nat=None, firewall=None
        runner = CliRunner()
        adapter_mock = MagicMock()
        adapter_mock.dump.return_value = device
        with runner.isolated_filesystem():
            Path("config.yaml").write_text("version: '1.0'\ntargets: {}\n")
            with (
                patch("routerless.cli._load", return_value=NetworkConfig(targets={"r": TARGET_CFG})),
                patch("routerless.cli._get_adapter", return_value=adapter_mock),
            ):
                result = runner.invoke(cli, ["import", "--target", "r", "--section", "nat", "config.yaml"])
            assert result.exit_code == 0, result.output
            assert "skipped" in result.output
            assert not Path("nat.yaml").exists()

    def test_config_dir_arg_defaults_output_to_that_dir(self) -> None:
        """Passing a directory as CONFIG should write files there, not to cwd."""
        device = _device_cfg(leases=[_LEASE_A])
        runner = CliRunner()
        adapter_mock = MagicMock()
        adapter_mock.dump.return_value = device
        with runner.isolated_filesystem():
            Path("mynet").mkdir()
            Path("mynet/configuration.yaml").write_text("version: '1.0'\ntargets: {}\n")
            with (
                patch("routerless.cli._load", return_value=NetworkConfig(targets={"r": TARGET_CFG})),
                patch("routerless.cli._get_adapter", return_value=adapter_mock),
            ):
                result = runner.invoke(cli, ["import", "--target", "r", "--section", "dhcp", "mynet"])
            assert result.exit_code == 0, result.output
            assert Path("mynet/dhcp.yaml").exists()
            assert not Path("dhcp.yaml").exists()


# ---------------------------------------------------------------------------
# cmd_import — file exists (diff + prompt)
# ---------------------------------------------------------------------------

class TestCmdImportExistingFile:
    def _run(self, device: NetworkConfig, existing_yaml: str, section: str, input: str) -> tuple[int, str, str]:
        runner = CliRunner()
        adapter_mock = MagicMock()
        adapter_mock.dump.return_value = device
        filename = {"dhcp": "dhcp.yaml", "nat": "nat.yaml", "firewall": "firewall.yaml"}[section]
        with runner.isolated_filesystem():
            Path("config.yaml").write_text("version: '1.0'\ntargets: {}\n")
            Path(filename).write_text(existing_yaml)
            with (
                patch("routerless.cli._load", return_value=NetworkConfig(targets={"r": TARGET_CFG})),
                patch("routerless.cli._get_adapter", return_value=adapter_mock),
            ):
                result = runner.invoke(
                    cli,
                    ["import", "--target", "r", "--section", section, "config.yaml"],
                    input=input,
                )
            final_content = Path(filename).read_text()
        return result.exit_code, result.output, final_content

    def test_no_diff_shows_no_differences(self) -> None:
        device = _device_cfg(leases=[_LEASE_A])
        device_data = device.dhcp.model_dump(exclude_none=True, exclude_unset=True)
        import yaml as _yaml
        existing = _yaml.dump(device_data, default_flow_style=False, allow_unicode=True, sort_keys=False)
        _, out, _ = self._run(device, existing, "dhcp", "")
        assert "no differences" in out

    def test_override_replaces_file(self) -> None:
        device = _device_cfg(leases=[_LEASE_A])
        existing = "static_leases:\n- name: OldDevice\n  mac: FF:FF:FF:FF:FF:FF\n  ip: 192.168.1.99\n"
        _, out, final = self._run(device, existing, "dhcp", "o\n")
        assert "override" in out.lower()
        parsed = yaml.safe_load(final)
        assert parsed["static_leases"][0]["mac"] == "AA:BB:CC:DD:EE:FF"

    def test_skip_leaves_file_unchanged(self) -> None:
        device = _device_cfg(leases=[_LEASE_A])
        existing = "static_leases:\n- name: OldDevice\n  mac: FF:FF:FF:FF:FF:FF\n  ip: 192.168.1.99\n"
        _, out, final = self._run(device, existing, "dhcp", "s\n")
        assert "skipped" in out
        assert final == existing

    def test_append_adds_new_entries(self) -> None:
        device = _device_cfg(leases=[_LEASE_A, _LEASE_B])
        existing = (
            "subnet: 192.168.1.0/24\ngateway: 192.168.1.1\n"
            "static_leases:\n- name: NAS\n  mac: AA:BB:CC:DD:EE:FF\n  ip: 192.168.1.20\n"
        )
        _, out, final = self._run(device, existing, "dhcp", "a\n")
        assert "append" in out.lower()
        parsed = yaml.safe_load(final)
        macs = [entry["mac"] for entry in parsed["static_leases"]]
        assert "AA:BB:CC:DD:EE:FF" in macs
        assert "11:22:33:44:55:66" in macs

    def test_append_nat_adds_new_forwards(self) -> None:
        pf_b = PortForward(name="SSH", external_port=22, internal_ip="192.168.1.10", internal_port=22)
        device = _device_cfg(pfs=[_PF_A, pf_b])
        existing = (
            "port_forwards:\n- name: Plex\n  external_port: 32400\n"
            "  protocol: tcp\n  internal_ip: 192.168.1.20\n  internal_port: 32400\n"
        )
        _, out, final = self._run(device, existing, "nat", "a\n")
        parsed = yaml.safe_load(final)
        assert len(parsed["port_forwards"]) == 2
        ports = [pf["external_port"] for pf in parsed["port_forwards"]]
        assert 22 in ports

    def test_shows_diff_when_file_differs(self) -> None:
        device = _device_cfg(leases=[_LEASE_A])
        existing = "static_leases:\n- name: OldDevice\n  mac: FF:FF:FF:FF:FF:FF\n  ip: 192.168.1.99\n"
        _, out, _ = self._run(device, existing, "dhcp", "s\n")
        assert "---" in out or "+++" in out  # unified diff markers
