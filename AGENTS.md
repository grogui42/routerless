# Routerless — Agent Instructions

Router-agnostic network configuration manager. Declarative YAML config → applied to Bbox Ultim, OpenWrt, and QNAP Qhora routers via adapter plugins.

## Quick Start

```bash
# Setup (Python 3.13+ required)
pip install -e ".[dev]"

# Run all tests (must stay green before and after any change)
pytest

# Run CLI
routerless validate config/configuration.yaml
routerless apply --target bbox --section dhcp config/configuration.yaml
routerless status --target bbox config/configuration.yaml
```

## Project Layout

```
routerless/
  cli.py             # Click CLI: validate, apply, dump, diff, status, devices, wifi
  yaml_loader.py     # Custom YAML loader: !include, !secret, !include_dir_*
  models/config.py   # Pydantic v2 models: NetworkConfig, DHCPConfig, NATConfig, FirewallConfig
  adapters/
    base.py          # BaseAdapter ABC — defines the contract
    bbox_ultim.py    # Bbox Ultim via HTTPS REST (cookie auth + btoken CSRF)
    openwrt.py       # OpenWrt via SSH + UCI commands
    qnap_qhora.py    # QNAP Qhora 301W — delegates to OpenWrtAdapter
config/
  configuration.yaml # Main config (targets + !include sections)
  secrets.yaml       # NOT committed — copy from secrets.yaml.example
tests/               # pytest, all HTTP mocked via unittest.mock.patch
```

## Architecture

```
CLI → load_config (yaml_loader) → parse_config (Pydantic) → NetworkConfig
    → _get_adapter(cfg, target_name) → BaseAdapter subclass
    → adapter.apply_dhcp / apply_nat / apply_firewall / dump()
```

Adapter registry is in `cli.py → _ADAPTER_MAP`. Adding a new adapter requires:
1. New `TargetType` enum value in `models/config.py`
2. New class in `adapters/` inheriting `BaseAdapter` (implement 4 abstract methods)
3. Register in `_ADAPTER_MAP` in `cli.py`
4. Tests mocking device I/O

## Config System (YAML Custom Tags)

| Tag | Behaviour |
|-----|-----------|
| `!include <file>` | Inline another YAML file (relative to current file) |
| `!include_dir_merge_list <dir>` | Merge all `*.yaml` in dir as a list — each file must be a list |
| `!include_dir_named <dir>` | Dict keyed by filename stem |
| `!secret <key>` | Resolve from `secrets.yaml`, searching upward, stopping at config root |

`!secret` always resolves relative to the config root directory (the directory of `configuration.yaml`), even when used inside an included sub-file.

## Bbox Ultim Adapter — Critical Notes

- **Auth:** Cookie-based. `POST /login` with `password` + `remember=1` and `Referer`/`Origin` headers.
  After login, `GET /device/token` returns a CSRF **btoken** stored as `self._btoken`.
  Every mutating request appends `?btoken=<token>` to the URL.
- **Base URL:** `https://mabbox.bytel.fr/api/v1` — the router redirects its local HTTP to this cloud relay.
- **Endpoints confirmed (reverse-engineered):**
  - `GET/POST/DELETE /dhcp/clients` — static DHCP reservations
  - `PUT /dhcp/clients/<id>` — update existing reservation in-place (avoids IP conflict vs delete+create)
  - `PUT /dhcp` — update dynamic pool range; fields: `dhcp.minaddress`, `dhcp.maxaddress`, `dhcp.leasetime`
  - `GET/POST/DELETE /nat/rules` — port-forward rules
  - `GET /firewall/rules` — firewall rules (**GET confirmed**; POST/DELETE not yet captured)
  - `GET /wan/ip`, `/lan/ip`, `/device`, `/wireless`, `/voip`, `/hosts` — read-only status
  - `PUT /wireless` with `radio.enable=1|0` — WiFi on/off
- **Response shape:** `[{"<section>": {"<subsection>": {"list": [...]}}}]` — use `_extract_list(data, *keys)`.
- **Rate limit:** 3 failed logins → 300 s (or 1200 s) lockout. Don't iterate passwords.
- **Firewall:** `GET /firewall/rules` is confirmed working (returns live rules with `description`, `direction`, `src`, `dest`, `action` fields). `POST`/`DELETE` endpoints not yet captured — use mitmproxy to discover (see `.github/prompts/discover-bbox-interface.prompt.md`). `apply_firewall()` raises `NotImplementedError` until then.
- **Protocol mapping:** `Protocol.BOTH` → `"all"` in NAT form body (not `"tcpudp"` — confirmed from GET response).
- **DHCP hostname vs name:**
  - `StaticLease.name` — friendly label (spaces allowed). Sent as `device` in POST/PUT (write-only, **not returned by GET**).
  - `StaticLease.hostname` — DNS-safe identifier (no spaces). Sent as `hostname` in POST/PUT and **returned by GET**.
  - `apply_dhcp` compares `lease.hostname or lease.name` against `existing["hostname"]` to decide create/update/skip.
  - `_plan_dhcp` compares `lease.hostname or lease.name` against `d.hostname or d.name`; reports `hostname: 'old' → 'new'`.
  - A `hostname` with spaces causes Bbox to return 400 "Invalid parameter" — always use `lease.hostname or lease.name`.

## OpenWrt / QNAP Qhora Adapter

- Communicates over **SSH + UCI commands** via `paramiko`.
- Host key policy: `RejectPolicy` (rejects unknown hosts).
- Idempotent: reads existing state before writing; adds/updates only changed entries.
- Qhora default SSH port: **22200**. SSH access requires WPS button hold (12 s).

## Testing Conventions

All tests in `tests/`. HTTP is mocked — never make real network calls in tests.

```python
# Helpers defined in tests/test_bbox.py
_adapter()                          # Creates BboxUltimAdapter with test target
_mock_http(adapter, mock_client)    # Returns (p_make, p_login, p_logout) patches
_bbox_resp(data, status=200)        # MagicMock response with .json() + .raise_for_status()

# Pattern
mock_client = MagicMock()
mock_client.get.return_value = _bbox_resp([{"dhcp": {"clients": {"list": [], "number": 0}}}])
p_make, p_login, p_logout = _mock_http(adapter, mock_client)
with p_make, p_login, p_logout:
    adapter.apply_dhcp(config)
mock_client.post.assert_called_once()
```

## Key Conventions

- **Python 3.13+** — use modern syntax: `str | None`, `list[...]`, `dict[...]`. No `Optional`, `List`, `Dict`.
- **Pydantic v2** — `model_dump(exclude_none=True, exclude_unset=True)` for serialization.
- **MAC addresses** normalized to uppercase colon-separated in Pydantic validators.
- **Secrets** never logged or printed. `secrets.yaml` is gitignored.
- Apply only what is requested — don't add docstrings, comments, or error handling for impossible scenarios beyond what exists.
