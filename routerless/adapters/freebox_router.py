"""Freebox router adapter.

Authentication uses the Freebox OS REST API at https://mafreebox.freebox.fr/api/v4/.
Requires an app_token which must be obtained through the first-time authorization flow
(user presses a button on the Freebox to grant access).

API Reference: https://dev.freebox.fr/sdk/os/

CERTIFICATE HANDLING
---------------------
Freebox uses self-signed certificates issued by either 'Freebox Root CA' (RSA) or
'Freebox ECC Root CA' (ECDSA). By default, routerless validates these using embedded
Root CA certificates. To disable SSL verification (not recommended), set verify_ssl=False
in your target configuration.

CONFIRMED ENDPOINTS
---------------------------
DHCP static leases:
  GET    /api/v4/dhcp/static_lease/          — list static leases
  GET    /api/v4/dhcp/static_lease/{id}      — get specific lease
  POST   /api/v4/dhcp/static_lease/          — create lease
  PUT    /api/v4/dhcp/static_lease/{id}      — update lease
  DELETE /api/v4/dhcp/static_lease/{id}      — delete lease

NAT / port-forwarding:
  GET    /api/v4/fw/redir/                   — list port forwarding rules
  GET    /api/v4/fw/redir/{id}               — get specific rule
  POST   /api/v4/fw/redir/                   — create rule
  PUT    /api/v4/fw/redir/{id}               — update rule
  DELETE /api/v4/fw/redir/{id}               — delete rule

Firewall: Not available in official API documentation.

AUTHENTICATION FLOW
---------------------
1. GET /api/v4/login/ — get initial challenge
2. POST /api/v4/login/authorize/ — request app token (needs user button press on Freebox)
3. GET /api/v4/login/authorize/{track_id} — poll for authorization status
4. POST /api/v4/login/session/ — open session with app_token + challenge-response
   - password = hmac-sha1(app_token, challenge)
5. Include X-Fbx-App-Auth: {session_token} in all subsequent requests

For routerless, we assume the app_token is already stored in config.password,
and we perform the login flow automatically on each operation.

RESPONSE FORMAT
---------------------------
Successful responses have:
  { "success": true, "result": {...} }

Errors:
  { "success": false, "error_code": "...", "msg": "..." }

"""
from __future__ import annotations

import hashlib
import hmac
from base64 import b64encode
from typing import Any

import httpx

from routerless.adapters.base import BaseAdapter
from routerless.certificates import FREEBOX_CA_BUNDLE
from routerless.models.config import (
    DHCPConfig,
    FirewallConfig,
    NATConfig,
    NetworkConfig,
    PortForward,
    Protocol,
    StaticLease,
    TargetType,
)
from routerless.models.status import AdapterStatus, ConnectedDevice, WifiRadio

_DEFAULT_TIMEOUT = 10.0
_BASE_URL = "https://mafreebox.freebox.fr/api/v4"
_APP_ID = "fr.freebox.routerless"
_APP_NAME = "Routerless"
_APP_VERSION = "1.0"
_DEVICE_NAME = "Routerless CLI"


class FreeboxRouterAdapter(BaseAdapter):
    """Manages Freebox router configuration via its REST API (v4).

    The adapter handles DHCP static leases and NAT port forwarding.
    Firewall rules are not available in the official Freebox OS API.

    Authentication uses app_token (stored in target.password) and requires
    an active session_token obtained via HMAC-SHA1 challenge-response.
    """

    TARGET_TYPE = TargetType.FREEBOX

    # Protocol mapping
    _PROTO_TO_FB = {Protocol.TCP: "tcp", Protocol.UDP: "udp", Protocol.BOTH: "tcp_udp"}
    _FB_TO_PROTO = {"tcp": Protocol.TCP, "udp": Protocol.UDP, "tcp_udp": Protocol.BOTH}

    def __init__(self, target) -> None:
        super().__init__(target)
        self._session_token: str | None = None

    # ------------------------------------------------------------------
    # HTTP session management
    # ------------------------------------------------------------------

    def _make_client(self) -> httpx.Client:
        """Create an httpx client with appropriate defaults."""
        # Determine SSL verification
        if self.target.verify_ssl:
            verify = str(FREEBOX_CA_BUNDLE)  # Use embedded Root CA bundle
        else:
            verify = False
        
        return httpx.Client(
            base_url=_BASE_URL,
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=True,
            verify=verify,
        )

    def _get_challenge(self, client: httpx.Client) -> str:
        """Fetch a fresh challenge for authentication."""
        resp = client.get("/login/")
        resp.raise_for_status()
        data = resp.json()
        if data.get("success"):
            return data.get("result", {}).get("challenge", "")
        raise RuntimeError(f"Failed to get challenge: {data.get('msg', 'unknown error')}")

    def _login(self, client: httpx.Client) -> None:
        """Authenticate using app_token and obtain a session_token.

        Raises httpx.HTTPStatusError on failure.
        """
        app_token = self.target.password
        if not app_token:
            raise ValueError("Freebox adapter requires 'password' field (app_token)")

        # Get challenge
        challenge = self._get_challenge(client)
        if not challenge:
            raise RuntimeError("Failed to obtain challenge from Freebox")

        # Compute HMAC-SHA1(app_token, challenge)
        password = self._compute_password(app_token, challenge)

        # Open session
        session_data = {
            "app_id": _APP_ID,
            "app_version": _APP_VERSION,
            "password": password,
        }
        resp = client.post("/login/session/", json=session_data)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"Login failed: {data.get('msg', 'unknown error')}")

        session_token = data.get("result", {}).get("session_token")
        if not session_token:
            raise RuntimeError("No session_token in login response")

        self._session_token = session_token

    def _logout(self, client: httpx.Client) -> None:
        """Close the session (best effort)."""
        try:
            if self._session_token:
                client.post(
                    "/login/logout/",
                    headers={"X-Fbx-App-Auth": self._session_token},
                )
        except httpx.HTTPError:
            pass
        finally:
            self._session_token = None

    @staticmethod
    def _compute_password(app_token: str, challenge: str) -> str:
        """Compute HMAC-SHA1(app_token, challenge) and return base64-encoded."""
        h = hmac.new(
            app_token.encode("utf-8"),
            challenge.encode("utf-8"),
            hashlib.sha1,
        )
        return h.hexdigest()

    def _auth_headers(self) -> dict[str, str]:
        """Return headers needed for authenticated requests."""
        if not self._session_token:
            raise RuntimeError("Not authenticated. Call _login() first.")
        return {"X-Fbx-App-Auth": self._session_token}

    def _get(self, client: httpx.Client, path: str) -> Any:
        """GET request with authentication."""
        resp = client.get(path, headers=self._auth_headers())
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            error = data.get("error_code", "unknown")
            msg = data.get("msg", "")
            raise RuntimeError(f"API error: {error} — {msg}")
        return data.get("result")

    def _post(self, client: httpx.Client, path: str, data: dict[str, Any]) -> Any:
        """POST request with authentication."""
        resp = client.post(path, json=data, headers=self._auth_headers())
        resp.raise_for_status()
        resp_data = resp.json()
        if not resp_data.get("success"):
            error = resp_data.get("error_code", "unknown")
            msg = resp_data.get("msg", "")
            raise RuntimeError(f"API error: {error} — {msg}")
        return resp_data.get("result")

    def _put(self, client: httpx.Client, path: str, data: dict[str, Any]) -> Any:
        """PUT request with authentication."""
        resp = client.put(path, json=data, headers=self._auth_headers())
        resp.raise_for_status()
        resp_data = resp.json()
        if not resp_data.get("success"):
            error = resp_data.get("error_code", "unknown")
            msg = resp_data.get("msg", "")
            raise RuntimeError(f"API error: {error} — {msg}")
        return resp_data.get("result")

    def _delete(self, client: httpx.Client, path: str) -> None:
        """DELETE request with authentication."""
        resp = client.delete(path, headers=self._auth_headers())
        resp.raise_for_status()
        resp_data = resp.json()
        if not resp_data.get("success"):
            error = resp_data.get("error_code", "unknown")
            msg = resp_data.get("msg", "")
            raise RuntimeError(f"API error: {error} — {msg}")

    # ------------------------------------------------------------------
    # DHCP static leases
    # ------------------------------------------------------------------

    def _list_dhcp_leases(self, client: httpx.Client) -> list[dict[str, Any]]:
        """Fetch list of static DHCP leases."""
        result = self._get(client, "/dhcp/static_lease/")
        return result if isinstance(result, list) else []

    def _create_dhcp_lease(self, client: httpx.Client, lease: StaticLease) -> None:
        """Create a static DHCP lease."""
        data = {
            "mac": lease.mac,
            "ip": lease.ip,
            "comment": lease.name,
        }
        self._post(client, "/dhcp/static_lease/", data)

    def _update_dhcp_lease(
        self, client: httpx.Client, lease_id: str, lease: StaticLease
    ) -> None:
        """Update an existing DHCP lease."""
        data = {
            "ip": lease.ip,
            "comment": lease.name,
        }
        self._put(client, f"/dhcp/static_lease/{lease_id}", data)

    def _delete_dhcp_lease(self, client: httpx.Client, lease_id: str) -> None:
        """Delete a DHCP lease by ID."""
        self._delete(client, f"/dhcp/static_lease/{lease_id}")

    def apply_dhcp(self, config: DHCPConfig) -> None:
        """Declaratively sync DHCP static leases.

        - Leases in box but not in config → deleted.
        - Leases in config but not in box → created.
        - Leases present in both but with changed IP → updated.
        """
        with self._make_client() as client:
            self._login(client)
            try:
                existing = self._list_dhcp_leases(client)
                existing_by_mac: dict[str, dict[str, Any]] = {
                    e["mac"].upper(): e for e in existing if e.get("mac") and e.get("id")
                }
                desired_by_mac: dict[str, StaticLease] = {
                    lease.mac.upper(): lease for lease in config.static_leases
                }

                # Pass 1 — deletions
                for mac, entry in existing_by_mac.items():
                    if mac not in desired_by_mac:
                        self._delete_dhcp_lease(client, entry["id"])
                    else:
                        lease = desired_by_mac[mac]
                        existing_ip = entry.get("ip", "")
                        if lease.ip != existing_ip:
                            self._delete_dhcp_lease(client, entry["id"])
                            # Will be re-created in pass 2

                # Pass 2 — creations
                for mac, lease in desired_by_mac.items():
                    existing_entry = existing_by_mac.get(mac)
                    if not existing_entry:
                        # New lease
                        self._create_dhcp_lease(client, lease)
                    elif lease.ip != existing_entry.get("ip", ""):
                        # IP changed during pass 1 deletion, now create
                        self._create_dhcp_lease(client, lease)
                    else:
                        # Check if comment (name) changed
                        existing_comment = existing_entry.get("comment", "")
                        if lease.name != existing_comment:
                            self._update_dhcp_lease(client, existing_entry["id"], lease)
            finally:
                self._logout(client)

    # ------------------------------------------------------------------
    # NAT / Port forwarding
    # ------------------------------------------------------------------

    def _list_nat_rules(self, client: httpx.Client) -> list[dict[str, Any]]:
        """Fetch list of port forwarding rules."""
        result = self._get(client, "/fw/redir/")
        return result if isinstance(result, list) else []

    def _create_nat_rule(self, client: httpx.Client, pf: PortForward) -> None:
        """Create a port forwarding rule."""
        data = {
            "enabled": True,
            "ip_proto": self._PROTO_TO_FB.get(pf.protocol, pf.protocol.value),
            "wan_port_start": pf.external_port,
            "wan_port_end": pf.external_port,
            "lan_ip": pf.internal_ip,
            "lan_port": pf.internal_port,
            "src_ip": pf.external_ip or "0.0.0.0",
            "comment": pf.name,
        }
        self._post(client, "/fw/redir/", data)

    def _update_nat_rule(
        self, client: httpx.Client, rule_id: int, pf: PortForward
    ) -> None:
        """Update an existing port forwarding rule."""
        data = {
            "enabled": True,
            "lan_ip": pf.internal_ip,
            "lan_port": pf.internal_port,
            "comment": pf.name,
        }
        self._put(client, f"/fw/redir/{rule_id}", data)

    def _delete_nat_rule(self, client: httpx.Client, rule_id: int) -> None:
        """Delete a port forwarding rule."""
        self._delete(client, f"/fw/redir/{rule_id}")

    def apply_nat(self, config: NATConfig) -> None:
        """Declaratively sync NAT port forwarding rules.

        Keyed on (external_port, protocol). Rules absent from desired config
        are deleted; missing rules are created; changed rules are updated.
        """
        with self._make_client() as client:
            self._login(client)
            try:
                existing = self._list_nat_rules(client)

                def _key(ext_port: int, proto: str) -> str:
                    return f"{ext_port}/{proto}"

                existing_by_key: dict[str, dict[str, Any]] = {
                    _key(int(r.get("wan_port_start", 0)), r.get("ip_proto", "tcp")): r
                    for r in existing
                    if r.get("id") is not None
                }
                desired_by_key: dict[str, PortForward] = {
                    _key(pf.external_port, self._PROTO_TO_FB.get(pf.protocol, pf.protocol.value)): pf
                    for pf in config.port_forwards
                }

                # Pass 1 — deletions
                for key, rule in existing_by_key.items():
                    if key not in desired_by_key:
                        self._delete_nat_rule(client, rule["id"])

                # Pass 2 — creations and updates
                for key, pf in desired_by_key.items():
                    existing_rule = existing_by_key.get(key)
                    if existing_rule:
                        # Check if anything changed
                        if (
                            pf.internal_ip != existing_rule.get("lan_ip", "")
                            or pf.internal_port != int(existing_rule.get("lan_port", 0))
                            or pf.name != str(existing_rule.get("comment", ""))
                        ):
                            self._update_nat_rule(client, existing_rule["id"], pf)
                    else:
                        # New rule
                        self._create_nat_rule(client, pf)
            finally:
                self._logout(client)

    # ------------------------------------------------------------------
    # Firewall rules — not available in official API
    # ------------------------------------------------------------------

    def apply_firewall(self, config: FirewallConfig) -> None:
        raise NotImplementedError(
            "Freebox firewall rules are not exposed in the official Freebox OS API.\n"
            "Only DHCP and NAT (port forwarding) are supported."
        )

    # ------------------------------------------------------------------
    # Dump — read current config
    # ------------------------------------------------------------------

    def dump(self) -> NetworkConfig:
        """Read current DHCP leases and NAT rules from the Freebox."""
        with self._make_client() as client:
            self._login(client)
            try:
                dhcp_leases = self._list_dhcp_leases(client)
                nat_rules = self._list_nat_rules(client)
            finally:
                self._logout(client)

        # Build DHCP config
        leases: list[StaticLease] = []
        for lease_data in dhcp_leases:
            if lease_data.get("mac") and lease_data.get("ip"):
                leases.append(StaticLease(
                    name=lease_data.get("comment") or lease_data.get("hostname", ""),
                    mac=lease_data["mac"],
                    ip=lease_data["ip"],
                    hostname=lease_data.get("hostname"),
                ))

        # Build NAT config
        port_forwards: list[PortForward] = []
        for rule in nat_rules:
            wan_start = rule.get("wan_port_start")
            wan_end = rule.get("wan_port_end")
            lan_port = rule.get("lan_port")
            lan_ip = rule.get("lan_ip")
            if not (wan_start and lan_port and lan_ip):
                continue

            raw_proto = str(rule.get("ip_proto", "tcp")).lower()
            protocol = self._FB_TO_PROTO.get(raw_proto, Protocol.TCP)
            port_forwards.append(PortForward(
                name=str(rule.get("comment") or f"nat_rule_{rule.get('id', 0)}"),
                protocol=protocol,
                external_port=int(wan_start),
                internal_ip=lan_ip,
                internal_port=int(lan_port),
                external_ip=rule.get("src_ip") if rule.get("src_ip") != "0.0.0.0" else None,
            ))

        from routerless.models.config import DHCPConfig, NATConfig

        return NetworkConfig(
            dhcp=DHCPConfig(
                subnet="192.168.1.0/24",
                gateway=self.target.host,
                static_leases=leases,
            ) if leases else None,
            nat=NATConfig(port_forwards=port_forwards) if port_forwards else None,
            firewall=None,
        )

    # ------------------------------------------------------------------
    # Optional: not implemented for Freebox
    # ------------------------------------------------------------------

    def get_status(self) -> AdapterStatus:
        raise NotImplementedError(
            "FreeboxRouterAdapter does not implement get_status()."
        )

    def get_devices(self, only_active: bool = True) -> list[ConnectedDevice]:
        raise NotImplementedError(
            "FreeboxRouterAdapter does not implement get_devices()."
        )

    def get_wifi(self) -> list[WifiRadio]:
        raise NotImplementedError(
            "FreeboxRouterAdapter does not implement get_wifi()."
        )

    def wifi_enable(self, enable: bool) -> None:
        raise NotImplementedError(
            "FreeboxRouterAdapter does not implement wifi_enable()."
        )
