---
mode: agent
description: "Add a new router adapter (new router brand/model) with full CLI support"
---

# Add a New Router Adapter

Use this prompt to scaffold a complete new adapter for a router brand or model not yet supported.

> **Before writing any code**, read the current versions of:
> - `routerless/adapters/base.py` — source of truth for abstract methods and optional overrides
> - `routerless/cli.py` — source of truth for `_ADAPTER_MAP` and which CLI commands exist
>
> Use their actual content rather than the examples below, which may lag behind.

## What you need to provide

- **Router name / brand** (e.g. "FritzBox", "Asus Merlin", "Synology Router")
- **Communication protocol** (REST API, SSH + CLI, SNMP, Telnet…)
- **Authentication method** (cookie, token, basic auth, SSH key…)
- **Base URL or access method** (e.g. `http://192.168.1.1/api`, SSH to port 22)

---

## Checklist — 5 files to create or modify

### 1. `routerless/models/config.py` — Add TargetType enum value

```python
class TargetType(str, Enum):
    BBOX_ULTIM = "bbox_ultim"
    OPENWRT    = "openwrt"
    QNAP_QHORA = "qnap_qhora"
    MY_ROUTER  = "my_router"   # ← add this
```

Also ensure `TargetConfig` has any new required fields (e.g. `api_key: str | None = None`).

### 2. `routerless/adapters/my_router.py` — New adapter class

```python
"""MyRouter adapter."""
from __future__ import annotations

from routerless.adapters.base import BaseAdapter
from routerless.models.config import (
    DHCPConfig, FirewallConfig, NATConfig, NetworkConfig, TargetType,
)
from routerless.models.status import AdapterStatus, ConnectedDevice, WifiRadio


class MyRouterAdapter(BaseAdapter):
    TARGET_TYPE = TargetType.MY_ROUTER

    # ── Abstract (mandatory) — inspect base.py for current list ─────────

    def apply_dhcp(self, config: DHCPConfig) -> None: ...
    def apply_nat(self, config: NATConfig) -> None: ...
    def apply_firewall(self, config: FirewallConfig) -> None: ...
    def dump(self) -> NetworkConfig: ...

    # ── Optional overrides (default: raise NotImplementedError) ─────────
    # Implement only if the router supports the feature.
    # Inspect base.py for the current list and exact signatures.

    def get_status(self) -> AdapterStatus: ...
    def get_devices(self, only_active: bool = True) -> list[ConnectedDevice]: ...
    def get_wifi(self) -> list[WifiRadio]: ...
    def wifi_enable(self, enable: bool) -> None: ...
```

**Patterns to follow:**
- **REST API** → copy `bbox_ultim.py`: httpx client, `_make_client()`, `_login()`, `_logout()`, `with _make_client() as client`
- **SSH + CLI** → copy `openwrt.py`: paramiko, `@contextmanager _ssh()`, `_run(client, cmd)` raising on non-zero exit
- Use `_extract_list(data, *keys)` pattern for nested API responses
- Declarative sync in `apply_*`: read current state → delete stale → create new → update changed

### 3. `routerless/cli.py` — Register in adapter map

```python
from routerless.adapters.my_router import MyRouterAdapter

_ADAPTER_MAP: dict[TargetType, type[BaseAdapter]] = {
    TargetType.BBOX_ULTIM:  BboxUltimAdapter,
    TargetType.OPENWRT:     OpenWrtAdapter,
    TargetType.QNAP_QHORA:  QnapQhoraAdapter,
    TargetType.MY_ROUTER:   MyRouterAdapter,  # ← add this
}
```

No other CLI changes are needed. All commands work generically through `BaseAdapter`:
- `apply`, `dump`, `diff` — available once the 4 abstract methods are implemented
- `plan` — compares local config against `dump()` output; free for any adapter
- `status`, `devices`, `wifi` — available if the optional methods are overridden

Verify the full command list in `cli.py` (`@cli.command(...)`) in case new commands were added since this prompt was written.

### 4. `examples/configuration.yaml` — Add a target example

```yaml
targets:
  myrouter:
    type: my_router
    host: !secret myrouter_host
    password: !secret myrouter_password
```

Add the corresponding secrets to `examples/secrets.yaml`.

### 5. `tests/test_my_router.py` — Tests (all I/O mocked)

```python
"""Tests for MyRouterAdapter — all network calls mocked."""
from __future__ import annotations
from unittest.mock import MagicMock, patch
import pytest
from routerless.adapters.my_router import MyRouterAdapter
from routerless.models.config import DHCPConfig, NATConfig, StaticLease, TargetConfig, TargetType

TARGET = TargetConfig(type=TargetType.MY_ROUTER, host="192.168.1.1", password="secret")

def _adapter() -> MyRouterAdapter:
    return MyRouterAdapter(TARGET)

# For REST adapters — mock httpx:
# p_make, p_login, p_logout = _mock_http(adapter, mock_client)
# with p_make, p_login, p_logout:
#     adapter.apply_dhcp(config)

# For SSH adapters — mock paramiko:
# with patch.object(adapter, "_ssh", return_value=_ssh_ctx(mock_client)):
#     adapter.apply_dhcp(config)
```

---

## AGENTS.md — Update adapter notes

Add a section to `AGENTS.md` under the appropriate heading describing:
- Auth method and base URL
- Confirmed endpoints
- Any quirks (rate limits, SSL, redirect behaviour)

---

## Validation

```bash
pytest                                          # must stay green
routerless validate examples/configuration.yaml  # config parses
routerless status --target myrouter examples/configuration.yaml  # live test
```
