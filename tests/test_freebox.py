"""Tests for FreeboxRouterAdapter — HTTP is mocked via httpx."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from routerless.adapters.freebox_router import FreeboxRouterAdapter
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
    type=TargetType.FREEBOX,
    host="192.168.1.254",
    password="test_app_token_value_here",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _adapter() -> FreeboxRouterAdapter:
    return FreeboxRouterAdapter(TARGET)


def _mock_http(adapter: FreeboxRouterAdapter, mock_client: MagicMock):
    """Return patches that intercept _make_client, _login, _logout."""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=mock_client)
    cm.__exit__ = MagicMock(return_value=False)
    p_make = patch.object(adapter, "_make_client", return_value=cm)
    # Set the session_token during login
    def mock_login(client):
        adapter._session_token = "test_session_token"
    p_login = patch.object(adapter, "_login", side_effect=mock_login)
    p_logout = patch.object(adapter, "_logout")
    return p_make, p_login, p_logout


def _fb_resp(result: Any = None, success: bool = True, error_code: str = "", msg: str = "") -> MagicMock:
    """Create a mock Freebox API response."""
    r = MagicMock()
    r.status_code = 200 if success else 400
    r.json.return_value = {
        "success": success,
        "result": result,
        "error_code": error_code if error_code else None,
        "msg": msg,
    }
    r.raise_for_status = MagicMock()
    return r


# ---------------------------------------------------------------------------
# Authentication tests
# ---------------------------------------------------------------------------

class TestAuthentication:
    def test_compute_password(self) -> None:
        """Test HMAC-SHA1 computation."""
        app_token = "test_token"
        challenge = "test_challenge"
        password = FreeboxRouterAdapter._compute_password(app_token, challenge)
        # Verify it's a hex string (SHA1 output is 40 hex chars)
        assert len(password) == 40
        assert all(c in "0123456789abcdef" for c in password)

    def test_login_success(self) -> None:
        """Test successful login flow."""
        adapter = _adapter()
        mock_client = MagicMock()
        # GET /login/ to get challenge
        mock_client.get.return_value = _fb_resp(result={"challenge": "test_challenge"})
        # POST /login/session/ to open session
        mock_client.post.return_value = _fb_resp(result={"session_token": "test_session_token"})

        adapter._login(mock_client)

        assert adapter._session_token == "test_session_token"
        # Verify GET was called for challenge
        get_calls = [c for c in mock_client.get.call_args_list if "/login/" in str(c)]
        assert len(get_calls) >= 1

    def test_login_no_challenge(self) -> None:
        """Test login fails when no challenge returned."""
        adapter = _adapter()
        mock_client = MagicMock()
        mock_client.get.return_value = _fb_resp(result={})

        with pytest.raises(RuntimeError, match="challenge"):
            adapter._login(mock_client)

    def test_auth_headers_requires_session(self) -> None:
        """Test that auth_headers raises if not authenticated."""
        adapter = _adapter()
        with pytest.raises(RuntimeError, match="Not authenticated"):
            adapter._auth_headers()

    def test_auth_headers_includes_token(self) -> None:
        """Test that auth_headers returns correct header."""
        adapter = _adapter()
        adapter._session_token = "my_session_token"
        headers = adapter._auth_headers()
        assert headers["X-Fbx-App-Auth"] == "my_session_token"


# ---------------------------------------------------------------------------
# DHCP tests
# ---------------------------------------------------------------------------

class TestApplyDhcp:
    def test_creates_new_lease(self) -> None:
        """Test creation of a new DHCP lease."""
        adapter = _adapter()
        config = DHCPConfig(
            subnet="192.168.1.0/24",
            gateway="192.168.1.254",
            static_leases=[StaticLease(name="Hub", mac="AA:BB:CC:DD:EE:FF", ip="192.168.1.10")],
        )
        mock_client = MagicMock()
        # List leases returns empty
        mock_client.get.return_value = _fb_resp(result=[])
        # Create returns success
        mock_client.post.return_value = _fb_resp(result={"id": "1", "mac": "AA:BB:CC:DD:EE:FF"})

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            adapter.apply_dhcp(config)

        # Should have GET (list) and POST (create)
        assert mock_client.get.called
        assert mock_client.post.called
        post_data = mock_client.post.call_args[1]["json"]
        assert post_data["mac"] == "AA:BB:CC:DD:EE:FF"
        assert post_data["ip"] == "192.168.1.10"
        assert post_data["comment"] == "Hub"

    def test_deletes_removed_lease(self) -> None:
        """Test deletion of a DHCP lease not in desired config."""
        adapter = _adapter()
        config = DHCPConfig(
            subnet="192.168.1.0/24",
            gateway="192.168.1.254",
            static_leases=[],  # empty
        )
        existing_leases = [
            {"id": "old_id", "mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.1.10"}
        ]
        mock_client = MagicMock()
        mock_client.get.return_value = _fb_resp(result=existing_leases)
        mock_client.delete.return_value = _fb_resp(result={})

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            adapter.apply_dhcp(config)

        # Should delete the lease
        mock_client.delete.assert_called_once()
        assert "old_id" in mock_client.delete.call_args[0][0]

    def test_updates_changed_ip(self) -> None:
        """Test that a lease with changed IP is deleted then recreated."""
        adapter = _adapter()
        config = DHCPConfig(
            subnet="192.168.1.0/24",
            gateway="192.168.1.254",
            static_leases=[StaticLease(name="Hub", mac="AA:BB:CC:DD:EE:FF", ip="192.168.1.99")],
        )
        existing_leases = [
            {"id": "lease_1", "mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.1.10", "comment": "Hub"}
        ]
        mock_client = MagicMock()
        mock_client.get.return_value = _fb_resp(result=existing_leases)
        mock_client.delete.return_value = _fb_resp(result={})
        mock_client.post.return_value = _fb_resp(result={"id": "new_id"})

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            adapter.apply_dhcp(config)

        # Should delete old and create new
        mock_client.delete.assert_called_once()
        mock_client.post.assert_called_once()
        post_data = mock_client.post.call_args[1]["json"]
        assert post_data["ip"] == "192.168.1.99"

    def test_skips_identical_lease(self) -> None:
        """Test that an unchanged lease is not modified."""
        adapter = _adapter()
        config = DHCPConfig(
            subnet="192.168.1.0/24",
            gateway="192.168.1.254",
            static_leases=[StaticLease(name="Hub", mac="AA:BB:CC:DD:EE:FF", ip="192.168.1.10")],
        )
        existing_leases = [
            {"id": "lease_1", "mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.1.10", "comment": "Hub"}
        ]
        mock_client = MagicMock()
        mock_client.get.return_value = _fb_resp(result=existing_leases)

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            adapter.apply_dhcp(config)

        # Should not delete or create
        mock_client.delete.assert_not_called()
        mock_client.post.assert_not_called()


# ---------------------------------------------------------------------------
# NAT tests
# ---------------------------------------------------------------------------

class TestApplyNat:
    def test_creates_new_rule(self) -> None:
        """Test creation of a new port forwarding rule."""
        adapter = _adapter()
        config = NATConfig(
            port_forwards=[
                PortForward(
                    name="SSH",
                    protocol=Protocol.TCP,
                    external_port=2222,
                    internal_ip="192.168.1.50",
                    internal_port=22,
                )
            ]
        )
        mock_client = MagicMock()
        # List rules returns empty
        mock_client.get.return_value = _fb_resp(result=[])
        # Create returns success
        mock_client.post.return_value = _fb_resp(result={"id": 1})

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            adapter.apply_nat(config)

        # Should have GET (list) and POST (create)
        assert mock_client.get.called
        mock_client.post.assert_called_once()
        post_data = mock_client.post.call_args[1]["json"]
        assert post_data["wan_port_start"] == 2222
        assert post_data["lan_ip"] == "192.168.1.50"
        assert post_data["lan_port"] == 22
        assert post_data["ip_proto"] == "tcp"

    def test_deletes_removed_rule(self) -> None:
        """Test deletion of a NAT rule not in desired config."""
        adapter = _adapter()
        config = NATConfig(port_forwards=[])  # empty

        existing_rules = [
            {"id": 5, "wan_port_start": 2222, "wan_port_end": 2222, "ip_proto": "tcp",
             "lan_ip": "192.168.1.50", "lan_port": 22}
        ]
        mock_client = MagicMock()
        mock_client.get.return_value = _fb_resp(result=existing_rules)
        mock_client.delete.return_value = _fb_resp(result={})

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            adapter.apply_nat(config)

        # Should delete the rule
        mock_client.delete.assert_called_once()
        assert "/5" in mock_client.delete.call_args[0][0]

    def test_updates_changed_rule(self) -> None:
        """Test that a rule with changed target is updated."""
        adapter = _adapter()
        config = NATConfig(
            port_forwards=[
                PortForward(
                    name="SSH",
                    protocol=Protocol.TCP,
                    external_port=2222,
                    internal_ip="192.168.1.99",  # changed
                    internal_port=22,
                )
            ]
        )
        existing_rules = [
            {"id": 5, "wan_port_start": 2222, "wan_port_end": 2222, "ip_proto": "tcp",
             "lan_ip": "192.168.1.50", "lan_port": 22, "comment": "SSH"}
        ]
        mock_client = MagicMock()
        mock_client.get.return_value = _fb_resp(result=existing_rules)
        mock_client.put.return_value = _fb_resp(result={})

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            adapter.apply_nat(config)

        # Should update the rule
        mock_client.put.assert_called_once()
        put_data = mock_client.put.call_args[1]["json"]
        assert put_data["lan_ip"] == "192.168.1.99"

    def test_skips_identical_rule(self) -> None:
        """Test that an unchanged rule is not modified."""
        adapter = _adapter()
        config = NATConfig(
            port_forwards=[
                PortForward(
                    name="SSH",
                    protocol=Protocol.TCP,
                    external_port=2222,
                    internal_ip="192.168.1.50",
                    internal_port=22,
                )
            ]
        )
        existing_rules = [
            {"id": 5, "wan_port_start": 2222, "wan_port_end": 2222, "ip_proto": "tcp",
             "lan_ip": "192.168.1.50", "lan_port": 22, "comment": "SSH"}
        ]
        mock_client = MagicMock()
        mock_client.get.return_value = _fb_resp(result=existing_rules)

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            adapter.apply_nat(config)

        # Should not update
        mock_client.put.assert_not_called()
        mock_client.post.assert_not_called()
        mock_client.delete.assert_not_called()

    def test_protocol_mapping(self) -> None:
        """Test that protocols are mapped correctly."""
        adapter = _adapter()

        assert adapter._PROTO_TO_FB[Protocol.TCP] == "tcp"
        assert adapter._PROTO_TO_FB[Protocol.UDP] == "udp"
        assert adapter._PROTO_TO_FB[Protocol.BOTH] == "tcp_udp"

        assert adapter._FB_TO_PROTO["tcp"] == Protocol.TCP
        assert adapter._FB_TO_PROTO["udp"] == Protocol.UDP
        assert adapter._FB_TO_PROTO["tcp_udp"] == Protocol.BOTH


# ---------------------------------------------------------------------------
# Firewall tests
# ---------------------------------------------------------------------------

class TestApplyFirewall:
    def test_not_implemented(self) -> None:
        """Test that firewall is not implemented."""
        from routerless.models.config import FirewallConfig

        adapter = _adapter()
        config = FirewallConfig()

        with pytest.raises(NotImplementedError, match="firewall"):
            adapter.apply_firewall(config)


# ---------------------------------------------------------------------------
# Dump tests
# ---------------------------------------------------------------------------

class TestDump:
    def test_dump_with_leases_and_rules(self) -> None:
        """Test dumping configuration with both DHCP and NAT."""
        adapter = _adapter()
        mock_client = MagicMock()

        dhcp_leases = [
            {"id": "1", "mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.1.10",
             "comment": "Hub", "hostname": "Hub"}
        ]
        nat_rules = [
            {"id": 5, "wan_port_start": 2222, "wan_port_end": 2222, "ip_proto": "tcp",
             "lan_ip": "192.168.1.50", "lan_port": 22, "comment": "SSH", "src_ip": "0.0.0.0"}
        ]

        mock_client.get.side_effect = [
            _fb_resp(result=dhcp_leases),
            _fb_resp(result=nat_rules),
        ]

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            result = adapter.dump()

        # Should have DHCP and NAT sections
        assert result.dhcp is not None
        assert len(result.dhcp.static_leases) == 1
        assert result.dhcp.static_leases[0].mac == "AA:BB:CC:DD:EE:FF"

        assert result.nat is not None
        assert len(result.nat.port_forwards) == 1
        assert result.nat.port_forwards[0].external_port == 2222

    def test_dump_empty(self) -> None:
        """Test dumping when no configuration exists."""
        adapter = _adapter()
        mock_client = MagicMock()
        mock_client.get.side_effect = [
            _fb_resp(result=[]),  # no DHCP leases
            _fb_resp(result=[]),  # no NAT rules
        ]

        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            result = adapter.dump()

        # Should be all None
        assert result.dhcp is None
        assert result.nat is None
        assert result.firewall is None
