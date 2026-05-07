with open("tests/test_openwrt.py", encoding="utf-8") as f:
    content = f.read()

INSERT = """
    def test_removes_stale_redirect(self) -> None:
        adapter = _make_adapter()
        config = NATConfig(port_forwards=[])
        existing = b"firewall.@redirect[2].name='OldRule'"
        mock_client = _mock_ssh_client({
            "uci show firewall | grep '@redirect.*\\\\.name='": existing,
        })

        with patch.object(adapter, "_ssh") as mock_ssh_ctx:
            mock_ssh_ctx.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_ssh_ctx.return_value.__exit__ = MagicMock(return_value=False)
            adapter.apply_nat(config)

        cmds = [c.args[0] for c in mock_client.exec_command.call_args_list]
        assert "uci delete firewall.@redirect[2]" in cmds
        assert "uci commit firewall" in cmds
        assert "uci add firewall redirect" not in cmds


# ---------------------------------------------------------------------------
# apply_firewall
# ---------------------------------------------------------------------------

class TestApplyFirewall:
    def test_new_rule_sends_uci_commands(self) -> None:
        adapter = _make_adapter()
        config = FirewallConfig(rules=[
            FirewallRule(name="Block IoT", src="iot", dest="wan"),
        ])
        mock_client = _mock_ssh_client({
            "uci show firewall | grep '@rule.*\\\\.name='": b"",
        })

        with patch.object(adapter, "_ssh") as mock_ssh_ctx:
            mock_ssh_ctx.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_ssh_ctx.return_value.__exit__ = MagicMock(return_value=False)
            adapter.apply_firewall(config)

        cmds = [c.args[0] for c in mock_client.exec_command.call_args_list]
        assert "uci add firewall rule" in cmds
        assert any("Block IoT" in c for c in cmds)
        assert any("iot" in c for c in cmds)
        assert any("wan" in c for c in cmds)
        assert "uci commit firewall" in cmds

    def test_removes_stale_rule(self) -> None:
        adapter = _make_adapter()
        config = FirewallConfig(rules=[])
        existing = b"firewall.@rule[0].name='OldRule'"
        mock_client = _mock_ssh_client({
            "uci show firewall | grep '@rule.*\\\\.name='": existing,
        })

        with patch.object(adapter, "_ssh") as mock_ssh_ctx:
            mock_ssh_ctx.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_ssh_ctx.return_value.__exit__ = MagicMock(return_value=False)
            adapter.apply_firewall(config)

        cmds = [c.args[0] for c in mock_client.exec_command.call_args_list]
        assert "uci delete firewall.@rule[0]" in cmds
        assert "uci commit firewall" in cmds
        assert "uci add firewall rule" not in cmds

"""

MARKER = "\n\n# ---------------------------------------------------------------------------\n# _parse_wifi_enabled\n# ---------------------------------------------------------------------------"
assert MARKER in content, "marker not found"
content = content.replace(MARKER, INSERT + "\n\n# ---------------------------------------------------------------------------\n# _parse_wifi_enabled\n# ---------------------------------------------------------------------------", 1)
with open("tests/test_openwrt.py", "w", encoding="utf-8") as f:
    f.write(content)
print("Done")
