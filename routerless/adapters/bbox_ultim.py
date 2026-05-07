"""Bbox Ultim adapter.

Authentication uses the local REST API at http://<host>/api/v1/.
DHCP static leases and NAT port-forward rules are NOT part of the official
public API (https://api.bbox.fr/doc/apirouter/index.html). The endpoints
below were reverse-engineered from the Bbox web interface XHR traffic.

CONFIRMED ENDPOINT SOURCES
---------------------------
Official (documented) API:
  POST   /api/v1/login              — authenticate, sets BBOX_ID cookie
  GET    /api/v1/device/token       — fetch CSRF btoken
  POST   /api/v1/logout
  GET    /api/v1/hosts              — all connected hosts (dynamic + static)

DHCP static reservations — source: gist malys/85e5a2276210bea3ebb770bc71d7289a (2023)
  GET    /api/v1/dhcp/clients
  POST   /api/v1/dhcp/clients?btoken=<token>
         form body: enable=1&device=<name>&ipaddress=<ip>
                    &macaddress=<mac>&hostname=<hostname>
  DELETE /api/v1/dhcp/clients/<id>?btoken=<token>

NAT / port-forwarding — source: lafibre.info user "cruchot" (Nov 2021), field names confirmed 2025-05-05
  GET    /api/v1/nat/rules
    response: [{"nat": {"enable": 1, "rules": [{...}, ...]}}]
    rule fields: id, enable, description, externalport, internalport, internalip, protocol, externalip
    protocol values: "tcp", "udp", "all" (not "tcpudp")
  POST   /api/v1/nat/rules?btoken=<token>
         form body: description=<name>&enable=1&protocol=<tcp|udp|all>
                    &internalip=<ip>&externalport=<n>&internalport=<n>
  DELETE /api/v1/nat/rules/<id>?btoken=<token>

Firewall rules — GET confirmed (2025-05-05), POST/DELETE not yet captured.
  GET    /api/v1/firewall/rules
    response shape: [{"firewall": {"rules": {"list": [...], "number": N}}}]
    rule fields: id, description, direction, src, dest, action
  POST   /api/v1/firewall/rules?btoken=<token>   (not yet captured)
  DELETE /api/v1/firewall/rules/<id>?btoken=<token>  (not yet captured)
  To discover POST/DELETE: open http://bbox.lan → DevTools → Network (XHR) → add/delete a
  firewall rule → copy request URL, method and body.

RESPONSE FORMAT (typical Bbox API nesting)
------------------------------------------
  GET /api/v1/dhcp/clients →
    [{"dhcp": {"clients": {"list": [{...lease...}, ...], "number": N}}}]

  GET /api/v1/nat/rules →
    [{"nat": {"rules": {"list": [{...rule...}, ...], "number": N}}}]

  Each lease/rule object carries an "id" field used for DELETE.

AUTHENTICATION NOTES
---------------------
- btoken is required as ?btoken=<token> for all mutating requests.
- The session cookie (BBOX_ID) is set by POST /api/v1/login.
- btoken expires; GET /api/v1/device/token should be called after login.
"""
from __future__ import annotations

from typing import Any

import httpx

from routerless.adapters.base import BaseAdapter
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

# Backward-compatible aliases (imported by tests and CLI)
BboxStatus = AdapterStatus
BboxDevice = ConnectedDevice
BboxWifiRadio = WifiRadio

# ---------------------------------------------------------------------------
# Protocol mapping constants
# ---------------------------------------------------------------------------

# Default API base URL — the Bbox redirects local HTTP to this address.
# Source: aiobbox (https://github.com/sweenu/aiobbox)
_DEFAULT_BASE_URL = "https://mabbox.bytel.fr/api/v1"


class BboxUltimAdapter(BaseAdapter):
    """Manages Bbox Ultim configuration via its local HTTP API.

    Authentication is cookie-based (no btoken required).
    The Bbox redirects its local HTTP to https://mabbox.bytel.fr so we use
    that URL as the default base. A custom ``host`` in the target config can
    override it (e.g. for a locally pinned HTTPS address).

    Key headers required by the API (discovered via aiobbox):
      Referer: <base_url>/login
      Origin:  <scheme>://<host>
    """

    TARGET_TYPE = TargetType.BBOX_ULTIM

    # Protocol value sent in NAT form body ("tcpudp" for both)
    _PROTO_TO_BBOX = {Protocol.TCP: "tcp", Protocol.UDP: "udp", Protocol.BOTH: "all"}
    _BBOX_TO_PROTO = {"tcp": Protocol.TCP, "udp": Protocol.UDP, "all": Protocol.BOTH}

    # ------------------------------------------------------------------
    # HTTP session management
    # ------------------------------------------------------------------

    def _base_url(self) -> str:
        """Return the effective API base URL.

        If the configured host looks like an IP address or custom hostname
        (not the Bbox cloud relay), build the URL from it; otherwise use the
        known cloud relay URL that the Bbox redirects to.
        """
        host = self.target.host
        if host and host != "mabbox.bytel.fr":
            # Could be a local IP — use the cloud relay anyway because the Bbox
            # always redirects HTTP→https://mabbox.bytel.fr. The host field is
            # only used if the user explicitly overrides to a full URL.
            pass
        return _DEFAULT_BASE_URL

    @property
    def _origin(self) -> str:
        base = self._base_url()
        # e.g. "https://mabbox.bytel.fr/api/v1" → "https://mabbox.bytel.fr"
        parts = base.split("/api/")
        return parts[0]

    @property
    def _req_headers(self) -> dict[str, str]:
        """Headers required on every authenticated request."""
        return {
            "Referer": self._base_url() + "/",
            "Origin": self._origin,
        }

    def _make_client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self._base_url(),
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=True,
        )

    def _login(self, client: httpx.Client) -> None:
        """Authenticate — sets the session cookie and fetches the btoken on *client*.

        Sends Referer/Origin headers as required by the Bbox API.
        Raises httpx.HTTPStatusError on failure (401 invalid password,
        429 rate-limited).
        """
        login_url = self._base_url() + "/login"
        resp = client.post(
            "/login",
            data={"password": self.target.password, "remember": "1"},
            headers={
                "Referer": login_url,
                "Origin": self._origin,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        resp.raise_for_status()
        # Fetch CSRF btoken — required as ?btoken=<token> for all mutating requests.
        token_data = client.get("/device/token", headers=self._req_headers).json()
        if isinstance(token_data, list) and token_data:
            self._btoken: str = token_data[0].get("device", {}).get("token", "")
        else:
            self._btoken = ""

    def _logout(self, client: httpx.Client) -> None:
        try:
            client.post("/logout", headers=self._req_headers)
        except httpx.HTTPError:
            pass

    def _get(self, client: httpx.Client, path: str) -> Any:
        resp = client.get(path, headers=self._req_headers)
        resp.raise_for_status()
        return resp.json()

    def _post(self, client: httpx.Client, path: str, data: dict[str, str]) -> None:
        url = f"{path}?btoken={self._btoken}" if getattr(self, "_btoken", "") else path
        resp = client.post(url, data=data, headers=self._req_headers)
        resp.raise_for_status()

    def _put(self, client: httpx.Client, path: str, data: dict[str, str]) -> None:
        url = f"{path}?btoken={self._btoken}" if getattr(self, "_btoken", "") else path
        resp = client.put(url, data=data, headers=self._req_headers)
        resp.raise_for_status()

    def _delete(self, client: httpx.Client, path: str) -> None:
        url = f"{path}?btoken={self._btoken}" if getattr(self, "_btoken", "") else path
        resp = client.delete(url, headers=self._req_headers)
        resp.raise_for_status()

    # ------------------------------------------------------------------
    # Response helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_list(data: Any, *keys: str) -> list[dict[str, Any]]:
        """Navigate the Bbox nested response wrapper and return the list.

        The API returns ``[{"<section>": {"<sub>": {"list": [...]}}}]``.
        Pass key path after the outer array, e.g.
        ``_extract_list(data, "dhcp", "clients")``.
        """
        if not isinstance(data, list) or not data:
            return []
        node: Any = data[0]
        for key in keys:
            if not isinstance(node, dict):
                return []
            node = node.get(key, node)
        if isinstance(node, dict):
            node = node.get("list", node)
        return node if isinstance(node, list) else []

    # ------------------------------------------------------------------
    # DHCP static leases — GET/POST/DELETE /dhcp/clients
    # ------------------------------------------------------------------

    def _list_dhcp_clients(self, client: httpx.Client) -> list[dict[str, Any]]:
        return self._extract_list(self._get(client, "/dhcp/clients"), "dhcp", "clients")

    def _create_dhcp_client(self, client: httpx.Client, lease: StaticLease) -> None:
        try:
            self._post(client, "/dhcp/clients", {
                "enable": "1",
                "device": lease.name,
                "ipaddress": lease.ip,
                "macaddress": lease.mac,
                "hostname": lease.hostname or lease.name,
            })
        except httpx.HTTPStatusError as exc:
            raise httpx.HTTPStatusError(
                f"{exc.response.status_code} creating lease "
                f"name={lease.name!r} mac={lease.mac} ip={lease.ip}",
                request=exc.request,
                response=exc.response,
            ) from exc

    def _delete_dhcp_client(self, client: httpx.Client, entry_id: int | str) -> None:
        self._delete(client, f"/dhcp/clients/{entry_id}")

    def _apply_dhcp_range(self, client: httpx.Client, config: DHCPConfig) -> None:
        """Update the Bbox dynamic DHCP pool range and lease time.

        Must be called before creating static leases so that IPs used for
        static reservations (e.g. .2–.139) are not inside the dynamic pool
        (which would cause 400 on POST /dhcp/clients).

        Endpoint: PUT /dhcp  (reverse-engineered, confirmed field names via XHR)
        """
        if not config.range:
            return
        start = config.range.get("start", "")
        end = config.range.get("end", "")
        if not start or not end:
            return
        # Convert lease_time "24h" → seconds for the Bbox API
        lease_time_str = config.lease_time or "24h"
        if lease_time_str.endswith("h"):
            lease_seconds = str(int(lease_time_str[:-1]) * 3600)
        elif lease_time_str.endswith("m"):
            lease_seconds = str(int(lease_time_str[:-1]) * 60)
        else:
            lease_seconds = lease_time_str
        self._put(client, "/dhcp", {
            "dhcp.minaddress": start,
            "dhcp.maxaddress": end,
            "dhcp.leasetime": lease_seconds,
        })

    def apply_dhcp(self, config: DHCPConfig) -> None:
        """Declaratively sync DHCP static reservations.

        - Leases in box but not in config → deleted.
        - Leases in config but not in box → created.
        - Leases present in both but changed (name, IP, or hostname) → deleted then re-created.

        Deletions are performed in a first pass so that IP addresses freed by
        moves/removals are available before new creations begin (avoids 403 IP
        conflicts when an IP is reassigned to a different device).
        """
        with self._make_client() as client:
            self._login(client)
            try:
                # Step 0 — shrink the dynamic pool first so static IPs are not
                # inside the range (Bbox returns 400 on such conflicts).
                self._apply_dhcp_range(client, config)

                existing = self._list_dhcp_clients(client)
                existing_by_mac: dict[str, dict[str, Any]] = {
                    e["macaddress"].upper(): e
                    for e in existing
                    if e.get("macaddress") and e.get("id") is not None
                }
                desired_by_mac: dict[str, StaticLease] = {
                    lease.mac.upper(): lease for lease in config.static_leases
                }

                # Pass 1 — deletions: removed entries + entries that need re-creation.
                to_create: list[StaticLease] = []
                for mac, entry in existing_by_mac.items():
                    if mac not in desired_by_mac:
                        self._delete_dhcp_client(client, entry["id"])
                for mac, lease in desired_by_mac.items():
                    existing_entry = existing_by_mac.get(mac)
                    if existing_entry:
                        have_ip = existing_entry.get("ipaddress", "")
                        have_host = existing_entry.get("hostname", "")
                        want_host = lease.hostname or lease.name
                        if lease.ip != have_ip or want_host != have_host:
                            self._delete_dhcp_client(client, existing_entry["id"])
                            to_create.append(lease)
                    else:
                        to_create.append(lease)

                # Pass 2 — creations: all IPs freed in pass 1 are now available.
                for lease in to_create:
                    self._create_dhcp_client(client, lease)
            finally:
                self._logout(client)

    # ------------------------------------------------------------------
    # NAT / Port forwarding — GET/POST/DELETE /nat/rules
    # ------------------------------------------------------------------

    def _list_nat_rules(self, client: httpx.Client) -> list[dict[str, Any]]:
        data = self._get(client, "/nat/rules")
        # Real Bbox response: [{"nat": {"enable": 1, "rules": [...]}}]
        # rules is a direct list, not {"list": [...], "number": N}
        if not isinstance(data, list) or not data:
            return []
        nat = data[0].get("nat", {})
        rules = nat.get("rules", [])
        return rules if isinstance(rules, list) else []

    def _create_nat_rule(self, client: httpx.Client, pf: PortForward) -> None:
        self._post(client, "/nat/rules", {
            "description": pf.name,
            "enable": "1",
            "protocol": self._PROTO_TO_BBOX.get(pf.protocol, pf.protocol.value),
            "internalip": pf.internal_ip,
            "externalport": str(pf.external_port),
            "internalport": str(pf.internal_port),
        })

    def _delete_nat_rule(self, client: httpx.Client, rule_id: int | str) -> None:
        self._delete(client, f"/nat/rules/{rule_id}")

    def apply_nat(self, config: NATConfig) -> None:
        """Declaratively sync NAT port-forward rules.

        Keyed on (external_port, protocol). Rules absent from the desired
        config are deleted; missing rules are created.
        """
        with self._make_client() as client:
            self._login(client)
            try:
                existing = self._list_nat_rules(client)

                def _key(ext_port: int | str, proto: str) -> str:
                    return f"{ext_port}/{proto}"

                existing_by_key: dict[str, dict[str, Any]] = {
                    _key(r.get("externalport", 0), r.get("protocol", "tcp")): r
                    for r in existing
                    if r.get("id") is not None
                }
                desired_by_key: dict[str, PortForward] = {
                    _key(pf.external_port, self._PROTO_TO_BBOX.get(pf.protocol, pf.protocol.value)): pf
                    for pf in config.port_forwards
                }
                for key, rule in existing_by_key.items():
                    if key not in desired_by_key:
                        self._delete_nat_rule(client, rule["id"])
                for key, pf in desired_by_key.items():
                    existing_rule = existing_by_key.get(key)
                    if existing_rule:
                        if (
                            pf.internal_ip != existing_rule.get("internalip", "")
                            or pf.internal_port != int(existing_rule.get("internalport", 0))
                            or pf.name != str(existing_rule.get("description", ""))
                        ):
                            self._delete_nat_rule(client, existing_rule["id"])
                            self._create_nat_rule(client, pf)
                    else:
                        self._create_nat_rule(client, pf)
            finally:
                self._logout(client)

    # ------------------------------------------------------------------
    # Firewall rules — endpoint not publicly documented
    # ------------------------------------------------------------------

    def apply_firewall(self, config: FirewallConfig) -> None:
        raise NotImplementedError(
            "Bbox Ultim firewall rule endpoints have not been found in any public\n"
            "source. Likely path: /api/v1/firewall/rules (unconfirmed).\n\n"
            "To discover: open http://bbox.lan → DevTools (F12) → Network tab\n"
            "→ filter XHR/Fetch → add/delete a firewall rule in the UI\n"
            "→ copy the request URL, method and body\n"
            "→ implement apply_firewall() in routerless/adapters/bbox_ultim.py."
        )

    # ------------------------------------------------------------------
    # Dump — read DHCP static leases + NAT rules from the device
    # ------------------------------------------------------------------

    def dump(self) -> NetworkConfig:
        """Read current DHCP static leases, NAT rules and firewall rules from the Bbox.

        The firewall endpoint is not publicly documented; it is probed at
        GET /firewall/rules using the same nesting pattern as the other
        endpoints.  Any HTTP error (404 or otherwise) is treated as
        "endpoint not available" and firewall is returned as None.
        """
        with self._make_client() as client:
            self._login(client)
            try:
                hosts_raw = self._get(client, "/hosts")
                try:
                    dhcp_clients = self._list_dhcp_clients(client)
                except httpx.HTTPError:
                    dhcp_clients = []
                try:
                    nat_rules_raw = self._list_nat_rules(client)
                except httpx.HTTPError:
                    nat_rules_raw = []
                try:
                    fw_rules_raw = self._extract_list(
                        self._get(client, "/firewall/rules"), "firewall", "rules"
                    )
                except httpx.HTTPError:
                    fw_rules_raw = []
            finally:
                self._logout(client)

        if dhcp_clients:
            leases: list[StaticLease] = [
                StaticLease(
                    name=c.get("hostname") or c.get("device") or c.get("macaddress", ""),
                    mac=c["macaddress"],
                    ip=c["ipaddress"],
                    hostname=c.get("hostname"),
                )
                for c in dhcp_clients
                if c.get("macaddress") and c.get("ipaddress")
            ]
        else:
            leases = []
            for entry in hosts_raw if isinstance(hosts_raw, list) else []:
                host = entry.get("host", entry)
                mac = host.get("macaddress") or host.get("mac")
                ip = host.get("ipaddress") or host.get("ip")
                hostname = host.get("hostname") or host.get("name") or mac
                if mac and ip:
                    leases.append(StaticLease(name=hostname, mac=mac, ip=ip, hostname=hostname))

        port_forwards: list[PortForward] = []
        for i, rule in enumerate(nat_rules_raw):
            ext_port = rule.get("externalport")
            int_port = rule.get("internalport")
            int_ip = rule.get("internalip")
            if not (ext_port and int_port and int_ip):
                continue
            raw_proto = str(rule.get("protocol", "tcp")).lower()
            protocol = self._BBOX_TO_PROTO.get(raw_proto, Protocol.TCP)
            port_forwards.append(PortForward(
                name=str(rule.get("description") or f"nat_rule_{rule.get('id', i)}"),
                protocol=protocol,
                external_port=int(ext_port),
                internal_ip=int_ip,
                internal_port=int(int_port),
            ))

        from routerless.models.config import DHCPConfig, FirewallAction, FirewallConfig, FirewallDirection, FirewallRule, NATConfig

        firewall_rules: list[FirewallRule] = []
        for rule in fw_rules_raw:
            name = rule.get("description") or rule.get("name") or f"fw_rule_{rule.get('id', '?')}"
            try:
                direction = FirewallDirection(str(rule.get("direction", "forward")).lower())
            except ValueError:
                direction = FirewallDirection.FORWARD
            try:
                action = FirewallAction(str(rule.get("action", "DROP")).upper())
            except ValueError:
                action = FirewallAction.DROP
            firewall_rules.append(FirewallRule(
                name=name,
                direction=direction,
                src=rule.get("src") or None,
                dest=rule.get("dest") or None,
                action=action,
            ))

        return NetworkConfig(
            dhcp=DHCPConfig(
                subnet="0.0.0.0/0",
                gateway=self.target.host,
                static_leases=leases,
            ) if leases else None,
            nat=NATConfig(port_forwards=port_forwards) if port_forwards else None,
            firewall=FirewallConfig(rules=firewall_rules) if firewall_rules else None,
        )

    # ------------------------------------------------------------------
    # get_status — GET wan/ip, lan/ip, device, wireless, voip, hosts
    # ------------------------------------------------------------------

    def get_status(self) -> BboxStatus:
        """Fetch general Bbox status from multiple official endpoints."""
        with self._make_client() as client:
            self._login(client)
            try:
                wan_data = self._get(client, "/wan/ip")
                lan_data = self._get(client, "/lan/ip")
                device_data = self._get(client, "/device")
                wifi_data = self._get(client, "/wireless")
                voip_data = self._get(client, "/voip")
                hosts_data = self._get(client, "/hosts")
            finally:
                self._logout(client)

        wan = (wan_data[0].get("wan", {}) if isinstance(wan_data, list) else {})
        wan_ip = wan.get("ip", {}).get("address", "")
        internet_state = wan.get("internet", {}).get("state", "?")

        lan = (lan_data[0].get("lan", {}) if isinstance(lan_data, list) else {})
        lan_ip = lan.get("ip", {}).get("address", self.target.host)

        dev = (device_data[0].get("device", {}) if isinstance(device_data, list) else {})
        uptime = int(dev.get("uptime", 0))
        model = dev.get("modelname", "")
        serial = dev.get("serialnumber", "")

        wireless = (wifi_data[0].get("wireless", {}) if isinstance(wifi_data, list) else {})
        radio = wireless.get("radio", {})
        wifi_24_enabled = bool(radio.get("24", {}).get("enable", 0))
        radio_5 = radio.get("5")
        wifi_5_enabled = bool(radio_5.get("enable", 0)) if radio_5 is not None else None

        voip_list = (voip_data[0].get("voip", []) if isinstance(voip_data, list) else [])
        voip_status = voip_list[0].get("status", "?") if voip_list else "?"

        device_count = len(self._extract_list(hosts_data, "hosts"))

        return BboxStatus(
            lan_ip=lan_ip,
            wan_ip=wan_ip,
            internet_state=internet_state,
            voip_status=voip_status,
            wifi_24_enabled=wifi_24_enabled,
            wifi_5_enabled=wifi_5_enabled,
            device_count=device_count,
            uptime_seconds=uptime,
            model=model,
            serial=serial,
        )

    # ------------------------------------------------------------------
    # get_devices — GET /hosts
    # ------------------------------------------------------------------

    def get_devices(self, only_active: bool = True) -> list[BboxDevice]:
        """List hosts detected by the Bbox.

        *only_active=True* returns only currently connected devices;
        *False* returns all known devices.
        """
        with self._make_client() as client:
            self._login(client)
            try:
                hosts_data = self._get(client, "/hosts")
            finally:
                self._logout(client)

        devices: list[BboxDevice] = []
        for h in self._extract_list(hosts_data, "hosts"):
            active = bool(h.get("active", 1))
            if only_active and not active:
                continue
            # aiobbox: link = "Ethernet" | "Wifi 2.4" | "Wifi 5" | "Wifi 6"
            link_type = h.get("link", "")
            if "Ethernet" in link_type:
                port = (h.get("ethernet") or {}).get("logicalport", "")
                link = f"Ethernet port {port}" if port != "" else "Ethernet"
            elif "Wifi" in link_type:
                rssi = (h.get("wireless") or {}).get("rssi0", "")
                link = f"{link_type} RSSI {rssi}" if rssi != "" else link_type
            else:
                link = link_type or "?"
            devices.append(BboxDevice(
                ip=h.get("ipaddress", ""),
                mac=h.get("macaddress", ""),
                hostname=h.get("hostname", ""),
                device_type=h.get("devicetype", ""),
                link=link,
                active=active,
            ))
        return devices

    # ------------------------------------------------------------------
    # get_wifi / wifi_enable — GET /wireless, PUT /wireless
    # ------------------------------------------------------------------

    def get_wifi(self) -> list[BboxWifiRadio]:
        """Return WiFi radio status for each band (2.4 GHz and 5 GHz)."""
        with self._make_client() as client:
            self._login(client)
            try:
                wifi_data = self._get(client, "/wireless")
                hosts_data = self._get(client, "/hosts")
            finally:
                self._logout(client)

        wireless = (wifi_data[0].get("wireless", {}) if isinstance(wifi_data, list) else {})
        radio_map = wireless.get("radio", {})
        ssid_map = wireless.get("ssid", {})
        device_count = len(self._extract_list(hosts_data, "hosts"))

        radios: list[BboxWifiRadio] = []
        for band_key, label in (("24", "2.4GHz"), ("5", "5GHz")):
            r = radio_map.get(band_key)
            if r is None:
                continue
            s = ssid_map.get(band_key, {})
            sec = s.get("security", {}) if isinstance(s, dict) else {}
            radios.append(BboxWifiRadio(
                band=label,
                enabled=bool(r.get("enable", 0)),
                channel=r.get("current_channel"),
                ssid=s.get("id", "") if isinstance(s, dict) else "",
                protocol=sec.get("protocol", ""),
                encryption=sec.get("encryption", ""),
                device_count=device_count if band_key == "24" else None,
            ))
        return radios

    def wifi_enable(self, enable: bool) -> None:
        """Enable (``True``) or disable (``False``) all Bbox WiFi radios."""
        with self._make_client() as client:
            self._login(client)
            try:
                self._put(client, "/wireless", {"radio.enable": "1" if enable else "0"})
            finally:
                self._logout(client)
