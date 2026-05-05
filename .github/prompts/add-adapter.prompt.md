---
mode: agent
description: "Add a new router adapter (new router brand/model) with full CLI support"
---

# Add a New Router Adapter

Use this prompt to scaffold a complete new adapter for a router brand or model not yet supported.

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

    # ── Apply methods (mandatory) ──────────────────────────────────────

    def apply_dhcp(self, config: DHCPConfig) -> None:
        ...  # implement or raise NotImplementedError

    def apply_nat(self, config: NATConfig) -> None:
        ...

    def apply_firewall(self, config: FirewallConfig) -> None:
        ...

    def dump(self) -> NetworkConfig:
        ...

    # ── Status / devices / wifi (optional — implement if the router supports it) ──

    def get_status(self) -> AdapterStatus:
        ...  # or leave as BaseAdapter default (raises NotImplementedError)

    def get_devices(self, only_active: bool = True) -> list[ConnectedDevice]:
        ...

    def get_wifi(self) -> list[WifiRadio]:
        ...

    def wifi_enable(self, enable: bool) -> None:
        ...
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

No other CLI changes are needed — all commands (`apply`, `dump`, `diff`, `status`, `devices`, `wifi`) work generically through `BaseAdapter`.

### 4. `config/configuration.yaml` — Add a target example

```yaml
targets:
  myrouter:
    type: my_router
    host: !secret myrouter_host
    password: !secret myrouter_password
```

Add the corresponding secrets to `config/secrets.yaml`.

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
routerless validate config/configuration.yaml  # config parses
routerless status --target myrouter config/configuration.yaml  # live test
```
