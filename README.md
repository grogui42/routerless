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
routerless validate

# 4. Preview what would change (like terraform plan)
routerless plan --target bbox

# 5. Apply
routerless apply --target bbox
```

## Configuration path resolution

All commands accept an optional `CONFIG` argument. Routerless resolves it as follows:

| Argument | Resolved file |
|----------|---------------|
| *(omitted)* | `./configuration.yaml` (current directory) |
| `./my-network` | `./my-network/configuration.yaml` |
| `./my-network/prod.yaml` | `./my-network/prod.yaml` |

```bash
# All equivalent when run from the config directory
routerless plan --target bbox
routerless plan --target bbox .
routerless plan --target bbox configuration.yaml

# Point at a different directory
routerless plan --target bbox ~/my-network
routerless plan --target bbox ~/my-network/configuration.yaml
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
routerless validate
routerless validate ~/my-network
routerless validate ~/my-network/configuration.yaml
```

### `plan`

Preview what `apply` would add, change, or delete — no writes to the device.

```bash
routerless plan --target bbox
routerless plan --target bbox ~/my-network
routerless plan --target openwrt --section dhcp --section nat ~/my-network/configuration.yaml
```

Output format:

```
Comparing local config against target 'bbox' (bbox_ultim)…

Section: dhcp  (+1  ~2)
  + ADD     lease "NAS-Server"       AA:BB:CC:DD:EE:FF  →  192.168.1.20
  ~ CHANGE  lease "Hub"              BB:CC:DD:EE:FF:00  ip: 192.168.1.10 → 192.168.1.11
  ~ CHANGE  lease "Home Assistant"   CC:DD:EE:FF:00:11  hostname: 'homeassistant' → 'home-assistant'

Section: nat  ✓ no changes

Plan: 1 to add, 2 to change.
      Run routerless apply --target bbox --section dhcp to apply.
```

### `apply`

Apply one or more sections to a target device.

```bash
# Apply all sections (from current directory)
routerless apply --target bbox

# Apply specific sections only
routerless apply --target openwrt --section dhcp
routerless apply --target openwrt --section nat --section firewall

# Point at a specific directory or file
routerless apply --target openwrt ~/my-network
routerless apply --target openwrt --section nat --section firewall ~/my-network/configuration.yaml
```

### `export`

Read the current device configuration and write it to section files.

```bash
# Export all sections to current directory
routerless export --target bbox

# Export only NAT rules
routerless export --target bbox --section nat

# Export from a specific config directory
routerless export --target bbox --output-dir ./exported ~/my-network
```

Writes `dhcp.yaml`, `nat.yaml`, and `firewall.yaml` into the output directory (`.` by default).
If a file already exists, a unified diff is displayed and you are prompted:

```
Section: dhcp  file: ./dhcp.yaml
--- dhcp.yaml (current)
+++ dhcp.yaml (device)
@@ -1,4 +1,4 @@
 static_leases:
-  - name: OldName
+  - name: NAS

Action [o=override / a=append / s=skip] > 
```

- **Override** — replace the file with the device content.
- **Append** — keep existing entries and add new ones from the device (deduplicated by MAC / port+protocol / rule name).
When exporting to a new directory, `export` also generates a `configuration.yaml`
with the target block and `!include` references for each exported section.

```bash
# Bootstrap a new config dir from a live device
routerless export --target bbox ../new-site
# → creates new-site/dhcp.yaml, nat.yaml, firewall.yaml, configuration.yaml
```

### `dump`

Read the current configuration from a device and print it as YAML.

```bash
routerless dump --target bbox
routerless dump --target openwrt --output backup.yaml ~/my-network
```

### `diff`

Show a unified diff between the local config file and the running device config.

```bash
routerless diff --target bbox --section dhcp
routerless diff --target bbox ~/my-network
```

### `status`

Show general device status (WAN/LAN IPs, WiFi state, uptime, connected devices).

```bash
routerless status --target bbox
routerless status --target openwrt ~/my-network
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
routerless devices --target bbox
routerless devices --target openwrt --all ~/my-network
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
routerless wifi status --target bbox
routerless wifi on  --target bbox
routerless wifi off --target openwrt ~/my-network
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
- name: "Home Assistant"          # friendly display label (any characters)
  mac: "AA:BB:CC:DD:EE:FF"
  ip: "192.168.1.20"
  hostname: home-assistant         # DNS-safe identifier sent to the router API

- name: NAS                       # if hostname is omitted, name is used as-is
  mac: "11:22:33:44:55:66"
  ip: "192.168.1.21"
```

`hostname` is the value pushed to the router (e.g. Bbox `hostname` field) and is
used by `plan` / `apply` to detect changes. It must be DNS-safe (letters, digits,
hyphens — no spaces or accents). When omitted, `name` is used in its place.
`name` is a human-readable label used only for display in CLI output.

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
pytest tests/test_export.py -v
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

- Auth is **cookie-based**. Login requires `Referer` and `Origin` headers; after login a CSRF btoken is fetched from `GET /device/token` and appended to every mutating request.
- The Bbox redirects local HTTP to `https://mabbox.bytel.fr/api/v1` — this is the effective base URL.
- **Rate limit:** 3 failed logins → up to 1200 s lockout.
- Firewall endpoint unknown — `apply_firewall()` raises `NotImplementedError`.
- **DHCP `hostname` field:** `GET /dhcp/clients` returns `hostname` but not `device`.
  The adapter sends `hostname = lease.hostname or lease.name` (DNS-safe, no spaces) to
  the Bbox API. The friendly `name` is sent as `device` (write-only, not returned by GET).
  `plan` and `apply` both compare on `hostname` to avoid phantom changes.

### OpenWrt / QNAP Qhora

- Uses UCI over SSH (paramiko). Host key policy: `RejectPolicy`.
- Qhora default SSH port: **22200**. Enable SSH by holding the WPS button for 12 s.
- `apply_*` methods are idempotent: they read current state before writing.
