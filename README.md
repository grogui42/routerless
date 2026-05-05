# routerless

Router-agnostic network configuration manager. Write your network config once in YAML, apply it to any supported router.

## Supported Routers

| Type | Authentication | Protocol |
|------|---------------|----------|
| **Bbox Ultim** (`bbox_ultim`) | Cookie (password) | HTTPS REST |
| **OpenWrt** (`openwrt`) | SSH key or password | SSH + UCI |
| **QNAP Qhora 301W** (`qnap_qhora`) | SSH password | SSH + UCI |

## Installation

**Requirements:** Python 3.13+

```bash
git clone https://github.com/you/routerless
cd routerless
pip install -e ".[dev]"
```

## Quick Start

```bash
# 1. Create a new config from template
routerless init ~/my-network
cd ~/my-network

# 2. Fill in your router credentials
cp secrets.yaml.example secrets.yaml
# edit secrets.yaml

# 3. Validate
routerless validate configuration.yaml

# 4. Preview what would change (like terraform plan)
routerless plan --target bbox configuration.yaml

# 5. Apply
routerless apply --target bbox configuration.yaml
```

## Commands

### `init`

Scaffold a new configuration directory with ready-to-edit template files.

```bash
routerless init ~/my-network
routerless init ./config --force   # overwrite existing files
```

Creates:

```
my-network/
  configuration.yaml   — main config (targets + !include sections)
  secrets.yaml.example — credential template (copy to secrets.yaml)
  dhcp.yaml            — DHCP settings and static leases
  nat.yaml             — port-forwarding rules
  firewall.yaml        — firewall rules
  .gitignore           — pre-configured to ignore secrets.yaml
```

Existing files are never overwritten unless `--force` is passed.

### `validate`

Parse and validate the configuration file and all its includes.

```bash
routerless validate ~/my-network/configuration.yaml
```

### `plan`

Preview what `apply` would add, change, or delete — no writes to the device.

```bash
routerless plan --target bbox config/configuration.yaml
routerless plan --target openwrt --section dhcp --section nat config/configuration.yaml
```

Output format:

```
Comparing local config against target 'bbox' (bbox_ultim)…

Section: dhcp  (+1  ~1)
  + ADD     lease "NAS-Server"  AA:BB:CC:DD:EE:FF  →  192.168.1.20
  ~ CHANGE  lease "Hub"         ip: 192.168.1.10 → 192.168.1.11

Section: nat  ✓ no changes

Plan: 1 to add, 1 to change.
      Run routerless apply --target bbox --section dhcp config/configuration.yaml to apply.
```

### `apply`

Apply one or more sections to a target device.

```bash
# Apply all sections
routerless apply --target bbox config/configuration.yaml

# Apply specific sections only
routerless apply --target openwrt --section dhcp config/configuration.yaml
routerless apply --target openwrt --section nat --section firewall config/configuration.yaml
```

### `dump`

Read the current configuration from a device and print it as YAML.

```bash
routerless dump --target bbox config/configuration.yaml
routerless dump --target openwrt --output backup.yaml config/configuration.yaml
```

### `diff`

Show a unified diff between the local config file and the running device config.

```bash
routerless diff --target bbox --section dhcp config/configuration.yaml
```

### `status`

Show general device status (WAN/LAN IPs, WiFi state, uptime, connected devices).

```bash
routerless status --target bbox config/configuration.yaml
routerless status --target openwrt config/configuration.yaml
```

```
Model             : Bbox Ultim  (ABC123)
LAN IP            : 192.168.1.254
WAN IP            : 82.x.x.x
Internet          : Connected
WiFi 2.4 GHz      : ON
WiFi 5 GHz        : ON
Devices           : 14
Uptime            : 12d 3h 42m
```

### `devices`

List connected (or all known) devices.

```bash
routerless devices --target bbox config/configuration.yaml
routerless devices --target openwrt --all config/configuration.yaml
```

```
Device IP        MAC                Hostname          Type Link
----------------------------------------------------------------
192.168.1.10     AA:BB:CC:DD:EE:FF  mynas                  Ethernet port 1
192.168.1.20     11:22:33:44:55:66  laptop                 Wifi 5 RSSI -62
```

### `wifi status / on / off`

Inspect or toggle WiFi radios.

```bash
routerless wifi status --target bbox config/configuration.yaml
routerless wifi on  --target bbox config/configuration.yaml
routerless wifi off --target openwrt config/configuration.yaml
```

## Configuration

### `config/configuration.yaml`

```yaml
version: "1.0"

targets:
  bbox:
    type: bbox_ultim
    host: !secret bbox_host
    password: !secret bbox_password

  openwrt:
    type: openwrt
    host: !secret openwrt_host
    ssh_user: root
    ssh_key: !secret openwrt_ssh_key
    ssh_port: 22

  qhora:
    type: qnap_qhora
    host: !secret qhora_host
    ssh_user: admin
    ssh_password: !secret qhora_ssh_password
    ssh_port: 22200

dhcp:     !include dhcp.yaml
nat:      !include nat.yaml
firewall: !include firewall.yaml
```

### `config/secrets.yaml` (never committed)

```yaml
bbox_host: 192.168.1.254
bbox_password: your_password

openwrt_host: 192.168.1.1
openwrt_ssh_key: ~/.ssh/id_ed25519

qhora_host: 192.168.1.2
qhora_ssh_password: your_password
```

Copy `config/secrets.yaml.example` to get started.

### `config/dhcp.yaml`

```yaml
subnet: "192.168.1.0/24"
gateway: "192.168.1.1"
static_leases: !include_dir_merge_list static_leases
```

Each file in `static_leases/` is a YAML list:

```yaml
# static_leases/servers.yaml
- name: NAS
  mac: "AA:BB:CC:DD:EE:FF"
  ip: "192.168.1.20"
  hostname: nas

- name: "Home Assistant"
  mac: "11:22:33:44:55:66"
  ip: "192.168.1.21"
```

### `config/nat.yaml`

```yaml
port_forwards:
  - name: "Home Assistant"
    protocol: tcp
    external_port: 8123
    internal_ip: "192.168.1.21"
    internal_port: 8123
```

### `config/firewall.yaml`

```yaml
rules:
  - name: "Block IoT to WAN"
    direction: forward
    src: iot
    dest: wan
    action: DROP
```

## YAML Custom Tags

| Tag | Behaviour |
|-----|-----------|
| `!include <file>` | Inline another YAML file (path relative to the current file) |
| `!include_dir_merge_list <dir>` | Merge all `*.yaml` files in a directory as a single list |
| `!include_dir_named <dir>` | Dict keyed by filename stem |
| `!secret <key>` | Look up a value from `secrets.yaml` (always resolved from config root) |

## Project Structure

```
routerless/
├── cli.py                  # Click CLI entry point
├── yaml_loader.py          # Custom YAML tags
├── models/
│   ├── config.py           # Pydantic v2 models (NetworkConfig, DHCPConfig…)
│   └── status.py           # Read-only dataclasses (AdapterStatus, WifiRadio…)
└── adapters/
    ├── base.py             # BaseAdapter ABC
    ├── bbox_ultim.py       # Bbox Ultim — HTTPS REST
    ├── openwrt.py          # OpenWrt — SSH + UCI
    └── qnap_qhora.py       # QNAP Qhora — delegates to OpenWrtAdapter
config/
├── configuration.yaml
├── secrets.yaml.example
├── dhcp.yaml
├── nat.yaml
├── firewall.yaml
└── static_leases/
tests/                      # pytest — all device I/O mocked
.github/
└── prompts/
    ├── add-adapter.prompt.md          # Guide: add a new router adapter
    ├── add-feature.prompt.md          # Guide: add a new CLI command/feature
    └── discover-bbox-interface.prompt.md  # Capture Bbox XHR via mitmproxy
```

## Development

```bash
# Run tests
pytest

# Run a specific test file
pytest tests/test_bbox.py -v

# Install in editable mode with dev dependencies
pip install -e ".[dev]"
```

All tests mock device I/O — no real router needed. Tests must stay green before and after every change.

## Adding a New Adapter

Use the built-in prompt:

```bash
# In VS Code with GitHub Copilot, reference:
# .github/prompts/add-adapter.prompt.md
```

Or follow these steps manually:
1. Add a `TargetType` enum value in `routerless/models/config.py`
2. Create `routerless/adapters/my_router.py` extending `BaseAdapter`
3. Register it in `_ADAPTER_MAP` in `routerless/cli.py`
4. Add tests in `tests/test_my_router.py`

## Adapter Notes

### Bbox Ultim

- Auth is **cookie-based** (no btoken). Login requires `Referer` and `Origin` headers.
- The Bbox redirects local HTTP to `https://mabbox.bytel.fr/api/v1` — this is the effective base URL.
- **Rate limit:** 3 failed logins → up to 1200 s lockout.
- Firewall endpoint unknown — `apply_firewall()` raises `NotImplementedError`.

### OpenWrt / QNAP Qhora

- Uses UCI over SSH (paramiko). Host key policy: `RejectPolicy`.
- Qhora default SSH port: **22200**. Enable SSH by holding the WPS button for 12 s.
- `apply_*` methods are idempotent: they read current state before writing.
