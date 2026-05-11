"""routerless CLI."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal, Sequence

import click
import yaml
from pydantic import ValidationError

from routerless.adapters.base import BaseAdapter
from routerless.adapters.bbox_ultim import BboxUltimAdapter
from routerless.adapters.freebox_router import FreeboxRouterAdapter
from routerless.adapters.openwrt import OpenWrtAdapter
from routerless.adapters.qnap_qhora import QnapQhoraAdapter
from routerless.models.config import NetworkConfig, TargetType, parse_config
from routerless.yaml_loader import SecretNotFoundError, load_config

if TYPE_CHECKING:
    from routerless.models.config import DHCPConfig, FirewallConfig, NATConfig

_ADAPTER_MAP: dict[TargetType, type[BaseAdapter]] = {
    TargetType.BBOX_ULTIM: BboxUltimAdapter,
    TargetType.FREEBOX: FreeboxRouterAdapter,
    TargetType.OPENWRT: OpenWrtAdapter,
    TargetType.QNAP_QHORA: QnapQhoraAdapter,
}

_SECTION_CHOICES = click.Choice(["dhcp", "nat", "firewall"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_config(path: str) -> str:
    """Return the config file path.

    Rules:
    - Existing directory → <dir>/configuration.yaml
    - Path with .yaml/.yml extension → use as-is
    - Anything else (non-existent dir, no extension) → <path>/configuration.yaml
    """
    p = Path(path)
    if p.suffix.lower() in (".yaml", ".yml"):
        return str(p)
    return str(p / "configuration.yaml")


def _load(config_path: str) -> NetworkConfig:
    config_path = _resolve_config(config_path)
    try:
        raw = load_config(config_path)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    except SecretNotFoundError as exc:
        raise click.ClickException(f"Secret resolution failed: {exc}") from exc
    try:
        return parse_config(raw)
    except ValidationError as exc:
        raise click.ClickException(f"Configuration invalid:\n{exc}") from exc


def _get_adapter(cfg: NetworkConfig, target_name: str) -> BaseAdapter:
    if target_name not in cfg.targets:
        available = ", ".join(cfg.targets) or "(none)"
        raise click.ClickException(
            f"Target '{target_name}' not found in configuration. Available: {available}"
        )
    target = cfg.targets[target_name]
    adapter_cls = _ADAPTER_MAP.get(target.type)
    if adapter_cls is None:
        raise click.ClickException(f"No adapter implemented for target type '{target.type.value}'")
    return adapter_cls(target)


def _resolve_sections(sections: Sequence[str]) -> list[str]:
    """Return the effective section list — all sections if none specified."""
    return list(sections) if sections else ["dhcp", "nat", "firewall"]


# ---------------------------------------------------------------------------
# CLI root
# ---------------------------------------------------------------------------

@click.group()
@click.version_option()
def cli() -> None:
    """routerless — router-agnostic network configuration manager."""


# ---------------------------------------------------------------------------
# init — template loading
# ---------------------------------------------------------------------------

def _get_template_dir() -> Path:
    """Return the path to the templates directory (inside the routerless package)."""
    return Path(__file__).parent / "templates"


def _load_template(name: str) -> str:
    """Load a template file by name (e.g., 'configuration.yaml', 'dhcp.yaml')."""
    template_path = _get_template_dir() / name
    if not template_path.exists():
        raise click.ClickException(
            f"Template file not found: {template_path}\n"
            f"Make sure the package is correctly installed."
        )
    return template_path.read_text(encoding="utf-8")


def _write_file(path: Path, content: str, force: bool) -> bool:
    """Write *content* to *path*. Return True if written, False if skipped."""
    if path.exists() and not force:
        click.echo(f"  {click.style('skip', fg='yellow')}  {path}  (already exists — use --force to overwrite)")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    click.echo(f"  {click.style('create', fg='green')}  {path}")
    return True


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@cli.command("init")
@click.argument("directory", default=".", type=click.Path(file_okay=False))
@click.option("--force", is_flag=True, default=False, help="Overwrite existing files.")
def cmd_init(directory: str, force: bool) -> None:
    """Create a new routerless configuration in DIRECTORY.

    Generates a ready-to-edit file tree:

    \b
      <directory>/
        configuration.yaml   — main config (targets + !include sections)
        secrets.yaml.example — credential template (copy to secrets.yaml)
        dhcp.yaml            — DHCP settings and static leases
        nat.yaml             — port-forwarding rules
        firewall.yaml        — firewall rules
        .gitignore           — ignores secrets.yaml

    \b
    Examples:
      routerless init
      routerless init ~/my-network --force
    """
    root = Path(directory)

    click.echo(f"Initialising routerless config in {click.style(str(root.resolve()), bold=True)}\n")

    # Template file mappings: (destination_path, template_filename)
    template_files = [
        (root / "configuration.yaml", "configuration.yaml"),
        (root / "secrets.yaml.example", "secrets.yaml.example"),
        (root / "dhcp.yaml", "dhcp.yaml"),
        (root / "nat.yaml", "nat.yaml"),
        (root / "firewall.yaml", "firewall.yaml"),
        (root / ".gitignore", ".gitignore"),
    ]

    written = 0
    for dest_path, template_name in template_files:
        content = _load_template(template_name)
        if _write_file(dest_path, content, force):
            written += 1

    click.echo("")
    if written:
        click.echo(click.style(f"Done! {written} file(s) created.", fg="green", bold=True))
        click.echo("")
        click.echo("Next steps:")
        click.echo(f"  1. cp {root / 'secrets.yaml.example'} {root / 'secrets.yaml'}")
        click.echo(f"  2. Edit {root / 'secrets.yaml'} with your router credentials")
        click.echo(f"  3. routerless validate {root / 'configuration.yaml'}")
        click.echo(f"  4. routerless plan --target bbox {root / 'configuration.yaml'}")
    else:
        click.echo("Nothing written — all files already exist. Use --force to overwrite.")


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

@cli.command("validate")
@click.argument("config", default=".", type=click.Path())
def cmd_validate(config: str) -> None:
    """Validate a configuration file (and all its !include references)."""
    cfg = _load(config)
    click.echo("Configuration is valid.")
    click.echo(f"  Targets  : {', '.join(cfg.targets) or '(none)'}")
    if cfg.dhcp:
        click.echo(f"  DHCP     : {len(cfg.dhcp.static_leases)} static lease(s)")
    if cfg.nat:
        click.echo(f"  NAT      : {len(cfg.nat.port_forwards)} port-forward(s)")
    if cfg.firewall:
        click.echo(f"  Firewall : {len(cfg.firewall.rules)} rule(s)")


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

@cli.command("apply")
@click.argument("config", default=".", type=click.Path())
@click.option("--target", "-t", required=True, help="Target name as defined in configuration.yaml")
@click.option(
    "--section", "-s",
    multiple=True,
    type=_SECTION_CHOICES,
    help="Section(s) to apply. Repeat to apply multiple. Defaults to all.",
)
def cmd_apply(config: str, target: str, section: tuple[str, ...]) -> None:
    """Apply configuration to a target device.

    Examples:\n
      routerless apply --target openwrt\n
      routerless apply --target openwrt --section dhcp\n
      routerless apply --target openwrt --section nat --section firewall
    """
    cfg = _load(config)
    adapter = _get_adapter(cfg, target)
    sections = _resolve_sections(section)

    for sec in sections:
        if sec == "dhcp":
            if cfg.dhcp is None:
                click.echo("  dhcp: no configuration, skipping.")
                continue
            click.echo(f"  Applying dhcp ({len(cfg.dhcp.static_leases)} static leases)…")
            adapter.apply_dhcp(cfg.dhcp)
            click.echo("  dhcp: done.")
        elif sec == "nat":
            if cfg.nat is None:
                click.echo("  nat: no configuration, skipping.")
                continue
            click.echo(f"  Applying nat ({len(cfg.nat.port_forwards)} port-forwards)…")
            adapter.apply_nat(cfg.nat)
            click.echo("  nat: done.")
        elif sec == "firewall":
            if cfg.firewall is None:
                click.echo("  firewall: no configuration, skipping.")
                continue
            click.echo(f"  Applying firewall ({len(cfg.firewall.rules)} rules)…")
            adapter.apply_firewall(cfg.firewall)
            click.echo("  firewall: done.")


# ---------------------------------------------------------------------------
# dump
# ---------------------------------------------------------------------------

@cli.command("dump")
@click.argument("config", default=".", type=click.Path())
@click.option("--target", "-t", required=True, help="Target name as defined in configuration.yaml")
@click.option(
    "--output", "-o",
    default=None,
    type=click.Path(),
    help="Write YAML output to this file instead of stdout.",
)
def cmd_dump(config: str, target: str, output: str | None) -> None:
    """Read current configuration from a device and print it as YAML."""
    cfg = _load(config)
    adapter = _get_adapter(cfg, target)
    dumped = adapter.dump()
    raw = dumped.model_dump(mode="json", exclude_none=True, exclude_unset=True)
    out = yaml.dump(raw, default_flow_style=False, allow_unicode=True, sort_keys=False)
    if output:
        Path(output).write_text(out, encoding="utf-8")
        click.echo(f"Configuration written to {output}")
    else:
        click.echo(out)


# ---------------------------------------------------------------------------
# diff  (stub — compares dump() vs local config)
# ---------------------------------------------------------------------------

@cli.command("diff")
@click.argument("config", default=".", type=click.Path())
@click.option("--target", "-t", required=True, help="Target name as defined in configuration.yaml")
@click.option(
    "--section", "-s",
    multiple=True,
    type=_SECTION_CHOICES,
    help="Section(s) to diff. Defaults to all.",
)
def cmd_diff(config: str, target: str, section: tuple[str, ...]) -> None:
    """Show differences between local config and running device config."""
    import difflib

    cfg = _load(config)
    adapter = _get_adapter(cfg, target)
    sections = _resolve_sections(section)

    local_raw = cfg.model_dump(mode="json", exclude_none=True, exclude_unset=True)
    device_cfg = adapter.dump()
    device_raw = device_cfg.model_dump(mode="json", exclude_none=True, exclude_unset=True)

    for sec in sections:
        local_sec = local_raw.get(sec)
        device_sec = device_raw.get(sec)
        local_yaml = yaml.dump({sec: local_sec}, default_flow_style=False, allow_unicode=True)
        device_yaml = yaml.dump({sec: device_sec}, default_flow_style=False, allow_unicode=True)
        diff = list(
            difflib.unified_diff(
                device_yaml.splitlines(keepends=True),
                local_yaml.splitlines(keepends=True),
                fromfile=f"device/{sec}",
                tofile=f"local/{sec}",
            )
        )
        if diff:
            click.echo("".join(diff))
        else:
            click.echo(f"{sec}: no differences.")


# ---------------------------------------------------------------------------
# plan helpers  (pure functions — testable without CLI)
# ---------------------------------------------------------------------------

PlanAction = Literal["add", "change", "remove"]
PlanItem = tuple[PlanAction, str]


def _plan_dhcp(
    local: "DHCPConfig | None",
    device: "DHCPConfig | None",
) -> list[PlanItem]:
    local_map = {s.mac.upper(): s for s in (local.static_leases if local else [])}
    device_map = {s.mac.upper(): s for s in (device.static_leases if device else [])}
    items: list[PlanItem] = []
    for mac, lease in local_map.items():
        if mac not in device_map:
            items.append(("add", f'lease "{lease.name}"  {mac}  →  {lease.ip}'))
        else:
            d = device_map[mac]
            changes: list[str] = []
            if lease.ip != d.ip:
                changes.append(f"ip: {d.ip} → {lease.ip}")
            local_host = lease.hostname or lease.name
            device_host = d.hostname or d.name
            if local_host != device_host:
                changes.append(f"hostname: {device_host!r} → {local_host!r}")
            if changes:
                items.append(("change", f'lease "{lease.name}"  {mac}  {", ".join(changes)}'))
    for mac, lease in device_map.items():
        if mac not in local_map:
            items.append(("remove", f'lease "{lease.name}"  {mac}  {lease.ip}'))
    return items


def _plan_nat(
    local: "NATConfig | None",
    device: "NATConfig | None",
) -> list[PlanItem]:
    local_map = {
        (pf.external_port, pf.protocol): pf
        for pf in (local.port_forwards if local else [])
    }
    device_map = {
        (pf.external_port, pf.protocol): pf
        for pf in (device.port_forwards if device else [])
    }
    items: list[PlanItem] = []
    for key, pf in local_map.items():
        proto = pf.protocol.value
        if key not in device_map:
            desc = f'rule "{pf.name}"  {proto}  :{pf.external_port} → {pf.internal_ip}:{pf.internal_port}'
            items.append(("add", desc))
        else:
            d = device_map[key]
            changes: list[str] = []
            if pf.internal_ip != d.internal_ip:
                changes.append(f"dest: {d.internal_ip} → {pf.internal_ip}")
            if pf.internal_port != d.internal_port:
                changes.append(f"dest-port: {d.internal_port} → {pf.internal_port}")
            if pf.name != d.name:
                changes.append(f"name: {d.name!r} → {pf.name!r}")
            if changes:
                items.append(("change", f'rule "{pf.name}"  {proto}  :{pf.external_port}  {", ".join(changes)}'))
    for key, pf in device_map.items():
        if key not in local_map:
            proto = pf.protocol.value
            desc = f'rule "{pf.name}"  {proto}  :{pf.external_port} → {pf.internal_ip}:{pf.internal_port}'
            items.append(("remove", desc))
    return items


def _plan_firewall(
    local: "FirewallConfig | None",
    device: "FirewallConfig | None",
) -> list[PlanItem]:
    local_map = {r.name: r for r in (local.rules if local else [])}
    device_map = {r.name: r for r in (device.rules if device else [])}
    items: list[PlanItem] = []
    for name, rule in local_map.items():
        if name not in device_map:
            items.append(("add", f'rule "{name}"  {rule.direction.value}  {rule.action.value}'))
        else:
            d = device_map[name]
            changes: list[str] = []
            if rule.action != d.action:
                changes.append(f"action: {d.action.value} → {rule.action.value}")
            if rule.direction != d.direction:
                changes.append(f"direction: {d.direction.value} → {rule.direction.value}")
            if rule.src != d.src:
                changes.append(f"src: {d.src!r} → {rule.src!r}")
            if rule.dest != d.dest:
                changes.append(f"dest: {d.dest!r} → {rule.dest!r}")
            if changes:
                items.append(("change", f'rule "{name}"  {", ".join(changes)}'))
    for name, rule in device_map.items():
        if name not in local_map:
            items.append(("remove", f'rule "{name}"  {rule.direction.value}  {rule.action.value}'))
    return items


_PLAN_SYMBOL: dict[PlanAction, str] = {
    "add":    "  + ADD   ",
    "change": "  ~ CHANGE",
    "remove": "  - REMOVE",
}
_PLAN_COLOR: dict[PlanAction, str] = {
    "add":    "green",
    "change": "yellow",
    "remove": "red",
}


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

@cli.command("plan")
@click.argument("config", default=".", type=click.Path())
@click.option("--target", "-t", required=True, help="Target name as defined in configuration.yaml")
@click.option(
    "--section", "-s",
    multiple=True,
    type=_SECTION_CHOICES,
    help="Section(s) to plan. Defaults to all.",
)
def cmd_plan(config: str, target: str, section: tuple[str, ...]) -> None:
    """Preview changes that would be applied (like terraform plan).

    Reads the current device configuration and compares it to the local YAML
    config file.  Changes are displayed with:

    \b
      + ADD     entry present in local config but not on device
      ~ CHANGE  entry present on both sides but with different values
      - REMOVE  entry on device that is absent from local config

    Examples:\n
      routerless plan --target bbox\n
      routerless plan --target openwrt --section dhcp --section nat
    """
    cfg = _load(config)
    adapter = _get_adapter(cfg, target)
    sections = _resolve_sections(section)

    target_type = cfg.targets[target].type.value
    click.echo(f"Comparing local config against target '{target}' ({target_type})…")
    click.echo("")

    try:
        device_cfg = adapter.dump()
    except Exception as exc:
        raise click.ClickException(f"Failed to read device configuration: {exc}") from exc

    total_add = total_change = total_remove = 0
    section_planners = {
        "dhcp":     lambda: _plan_dhcp(cfg.dhcp, device_cfg.dhcp),
        "nat":      lambda: _plan_nat(cfg.nat, device_cfg.nat),
        "firewall": lambda: _plan_firewall(cfg.firewall, device_cfg.firewall),
    }

    for sec in sections:
        local_sec = getattr(cfg, sec, None)

        if local_sec is None:
            header = click.style(f"Section: {sec}", bold=True)
            click.echo(header + "  " + click.style("(no local config — skipped)", dim=True))
            click.echo("")
            continue

        items = section_planners[sec]()

        n_add = sum(1 for a, _ in items if a == "add")
        n_change = sum(1 for a, _ in items if a == "change")
        n_remove = sum(1 for a, _ in items if a == "remove")
        total_add += n_add
        total_change += n_change
        total_remove += n_remove

        header = click.style(f"Section: {sec}", bold=True)
        if local_sec is None:
            click.echo(f"{header}  " + click.style("(no local config — skipped)", dim=True))
        elif not items:
            click.echo(f"{header}  " + click.style("✓ no changes", fg="green"))
        else:
            summary = "  ".join(filter(None, [
                click.style(f"+{n_add}", fg="green")   if n_add    else "",
                click.style(f"~{n_change}", fg="yellow") if n_change else "",
                click.style(f"-{n_remove}", fg="red")  if n_remove else "",
            ]))
            click.echo(f"{header}  ({summary})")
            for action, desc in items:
                symbol = click.style(_PLAN_SYMBOL[action], fg=_PLAN_COLOR[action], bold=True)
                click.echo(f"{symbol}  {desc}")
        click.echo("")

    # Summary
    if total_add == 0 and total_change == 0 and total_remove == 0:
        click.echo(click.style("Plan: no changes — device is already in sync.", fg="green", bold=True))
    else:
        parts: list[str] = []
        if total_add:
            parts.append(click.style(f"{total_add} to add", fg="green", bold=True))
        if total_change:
            parts.append(click.style(f"{total_change} to change", fg="yellow", bold=True))
        if total_remove:
            parts.append(click.style(f"{total_remove} to destroy", fg="red", bold=True))
        click.echo("Plan: " + ", ".join(parts) + ".")
        sec_flags = " ".join(f"--section {s}" for s in sections) if len(sections) < 3 else ""
        apply_cmd = f"routerless apply --target {target} {sec_flags} {config}".strip()
        click.echo(f"      Run {click.style(apply_cmd, bold=True)} to apply.")


# ---------------------------------------------------------------------------
# Uptime helper
# ---------------------------------------------------------------------------


def _fmt_uptime(seconds: int) -> str:
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# status  (Bbox-specific)
# ---------------------------------------------------------------------------

@cli.command("status")
@click.argument("config", default=".", type=click.Path())
@click.option("--target", "-t", required=True, help="Target name as defined in configuration.yaml")
def cmd_status(config: str, target: str) -> None:
    """Show device status (WAN/LAN, WiFi, uptime…)."""
    cfg = _load(config)
    adapter = _get_adapter(cfg, target)
    try:
        s = adapter.get_status()
    except NotImplementedError as exc:
        raise click.ClickException(str(exc)) from exc

    col = 18
    model_line = s.model
    if s.serial:
        model_line += f"  ({s.serial})"
    click.echo(f"{'Model':<{col}}: {model_line}")
    if s.lan_ip:
        click.echo(f"{'LAN IP':<{col}}: {s.lan_ip}")
    if s.wan_ip:
        click.echo(f"{'WAN IP':<{col}}: {s.wan_ip}")
    if s.internet_state:
        click.echo(f"{'Internet':<{col}}: {s.internet_state}")
    if s.voip_status:
        click.echo(f"{'VoIP':<{col}}: {s.voip_status}")
    if s.wifi_24_enabled is not None:
        click.echo(f"{'WiFi 2.4 GHz':<{col}}: {'ON' if s.wifi_24_enabled else 'OFF'}")
    if s.wifi_5_enabled is not None:
        click.echo(f"{'WiFi 5 GHz':<{col}}: {'ON' if s.wifi_5_enabled else 'OFF'}")
    click.echo(f"{'Devices':<{col}}: {s.device_count}")
    click.echo(f"{'Uptime':<{col}}: {_fmt_uptime(s.uptime_seconds)}")


# ---------------------------------------------------------------------------
# devices  (Bbox-specific)
# ---------------------------------------------------------------------------

@cli.command("devices")
@click.argument("config", default=".", type=click.Path())
@click.option("--target", "-t", required=True, help="Target name as defined in configuration.yaml")
@click.option("--all", "show_all", is_flag=True, default=False, help="Include inactive devices.")
def cmd_devices(config: str, target: str, show_all: bool) -> None:
    """List connected devices.

    By default only active (currently connected) devices are shown.
    Use --all to include previously seen but currently offline devices.
    """
    cfg = _load(config)
    adapter = _get_adapter(cfg, target)
    try:
        devices = adapter.get_devices(only_active=not show_all)
    except NotImplementedError as exc:
        raise click.ClickException(str(exc)) from exc

    if not devices:
        click.echo("No devices found.")
        return

    # Column widths
    ip_w = max(len(d.ip) for d in devices) + 2
    mac_w = 19
    host_w = max((len(d.hostname) for d in devices), default=8) + 2
    type_w = 5

    header = (
        f"{'Device IP':<{ip_w}}"
        f"{'MAC':<{mac_w}}"
        f"{'Hostname':<{host_w}}"
        f"{'Type':<{type_w}}"
        f"Link"
    )
    click.echo(header)
    click.echo("-" * len(header))

    for d in devices:
        status_mark = "" if d.active else " [offline]"
        click.echo(
            f"{d.ip:<{ip_w}}"
            f"{d.mac:<{mac_w}}"
            f"{d.hostname:<{host_w}}"
            f"{d.device_type:<{type_w}}"
            f"{d.link}{status_mark}"
        )


# ---------------------------------------------------------------------------
# wifi  (Bbox-specific, with on/off subcommands)
# ---------------------------------------------------------------------------

@cli.group("wifi")
def grp_wifi() -> None:
    """Manage and inspect Bbox WiFi."""


@grp_wifi.command("status")
@click.argument("config", default=".", type=click.Path())
@click.option("--target", "-t", required=True, help="Target name as defined in configuration.yaml")
def cmd_wifi_status(config: str, target: str) -> None:
    """Show WiFi radio status (SSID, channel, encryption, device count)."""
    cfg = _load(config)
    adapter = _get_adapter(cfg, target)
    try:
        radios = adapter.get_wifi()
    except NotImplementedError as exc:
        raise click.ClickException(str(exc)) from exc

    if not radios:
        click.echo("No WiFi information available.")
        return

    header = f"{'Band':<10}{'Enabled':<9}{'Ch':<5}{'SSID':<24}{'Protocol':<14}{'Encryption':<12}Devices"
    click.echo(header)
    click.echo("-" * len(header))
    for r in radios:
        enabled = "ON" if r.enabled else "OFF"
        ch = str(r.channel) if r.channel is not None else "-"
        devs = str(r.device_count) if r.device_count is not None else ""
        click.echo(
            f"{r.band:<10}{enabled:<9}{ch:<5}{r.ssid:<24}{r.protocol:<14}{r.encryption:<12}{devs}"
        )


@grp_wifi.command("on")
@click.argument("config", default=".", type=click.Path())
@click.option("--target", "-t", required=True, help="Target name as defined in configuration.yaml")
def cmd_wifi_on(config: str, target: str) -> None:
    """Enable WiFi radios."""
    cfg = _load(config)
    adapter = _get_adapter(cfg, target)
    try:
        adapter.wifi_enable(True)
    except NotImplementedError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("WiFi enabled.")


@grp_wifi.command("off")
@click.argument("config", default=".", type=click.Path())
@click.option("--target", "-t", required=True, help="Target name as defined in configuration.yaml")
def cmd_wifi_off(config: str, target: str) -> None:
    """Disable WiFi radios."""
    cfg = _load(config)
    adapter = _get_adapter(cfg, target)
    try:
        adapter.wifi_enable(False)
    except NotImplementedError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("WiFi disabled.")


# ---------------------------------------------------------------------------
# import helpers  (pure functions — testable without CLI)
# ---------------------------------------------------------------------------

_SECTION_FILENAME: dict[str, str] = {
    "dhcp":     "dhcp.yaml",
    "nat":      "nat.yaml",
    "firewall": "firewall.yaml",
}


def _merge_section(sec: str, existing: dict, device: dict) -> dict:
    """Return *existing* with new entries from *device* appended (no duplicates)."""
    if sec == "dhcp":
        existing_macs = {str(e.get("mac", "")).upper() for e in existing.get("static_leases", [])}
        new_leases = [
            entry for entry in device.get("static_leases", [])
            if str(entry.get("mac", "")).upper() not in existing_macs
        ]
        result = dict(existing)
        result["static_leases"] = list(existing.get("static_leases", [])) + new_leases
        return result
    if sec == "nat":
        existing_keys = {
            (pf.get("external_port"), pf.get("protocol", "tcp"))
            for pf in existing.get("port_forwards", [])
        }
        new_pfs = [
            pf for pf in device.get("port_forwards", [])
            if (pf.get("external_port"), pf.get("protocol", "tcp")) not in existing_keys
        ]
        result = dict(existing)
        result["port_forwards"] = list(existing.get("port_forwards", [])) + new_pfs
        return result
    if sec == "firewall":
        existing_names = {r.get("name") for r in existing.get("rules", [])}
        new_rules = [
            r for r in device.get("rules", [])
            if r.get("name") not in existing_names
        ]
        result = dict(existing)
        result["rules"] = list(existing.get("rules", [])) + new_rules
        return result
    return {**existing, **device}


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

@cli.command("import")
@click.argument("config", default=".", type=click.Path())
@click.option("--target", "-t", required=True, help="Target name as defined in configuration.yaml")
@click.option(
    "--section", "-s",
    multiple=True,
    type=_SECTION_CHOICES,
    help="Section(s) to import. Defaults to all.",
)
@click.option(
    "--output-dir", "-o",
    default=".",
    type=click.Path(file_okay=False),
    help="Directory to write imported files into. Defaults to config directory.",
)
def cmd_import(config: str, target: str, section: tuple[str, ...], output_dir: str) -> None:
    """Import current device configuration to YAML files.

    Reads the live configuration from TARGET and writes section files
    (dhcp.yaml, nat.yaml, firewall.yaml) into OUTPUT_DIR.

    If a file already exists, a unified diff is shown and you are prompted
    to choose: Override, Append (add new entries only), or Skip.

    \b
    The CONFIG argument is optional. If the resolved path does not contain a
    configuration file but a configuration.yaml exists in the current directory,
    the given path is used as the output directory instead.

    \b
    Examples:
      routerless import --target bbox
      routerless import --target bbox --section nat ../my-network-import
      routerless import --target bbox --output-dir ./imported
    """
    import difflib

    # Convenience: if the given config path has no configuration file but looks
    # like an output directory (no .yaml extension), redirect it to --output-dir
    # and fall back to the current directory's config.
    resolved = _resolve_config(config)
    if not Path(resolved).exists() and output_dir == "." and Path(config).suffix.lower() not in (".yaml", ".yml"):
        fallback = _resolve_config(".")
        if Path(fallback).exists():
            output_dir = config
            config = "."

    # Default output_dir to the config's parent directory when the user passed
    # a config path but did not explicitly specify --output-dir.
    if output_dir == ".":
        output_dir = str(Path(_resolve_config(config)).parent)

    cfg = _load(config)
    adapter = _get_adapter(cfg, target)
    sections = _resolve_sections(section)

    try:
        device_cfg = adapter.dump()
    except Exception as exc:
        raise click.ClickException(f"Failed to read device configuration: {exc}") from exc

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    created_sections: list[str] = []

    for sec in sections:
        device_sec = getattr(device_cfg, sec, None)
        if device_sec is None:
            click.echo(click.style(f"  {sec}", bold=True) + ": no data from device — skipped.")
            continue

        out_path = out_dir / _SECTION_FILENAME[sec]
        device_data = device_sec.model_dump(mode="json", exclude_none=True, exclude_unset=True)
        device_yaml = yaml.dump(
            device_data, default_flow_style=False, allow_unicode=True, sort_keys=False
        )

        if not out_path.exists():
            out_path.write_text(device_yaml, encoding="utf-8")
            click.echo(click.style("  create", fg="green") + f"  {out_path}")
            created_sections.append(sec)
            continue

        existing_text = out_path.read_text(encoding="utf-8")
        if existing_text.strip() == device_yaml.strip():
            msg = click.style(f"  {sec}", bold=True) + f"  {out_path}  " + click.style("✓ no differences", fg="green")
            click.echo(msg)
            continue

        diff_lines = list(difflib.unified_diff(
            existing_text.splitlines(keepends=True),
            device_yaml.splitlines(keepends=True),
            fromfile=f"{out_path.name} (current)",
            tofile=f"{out_path.name} (device)",
        ))
        click.echo(click.style(f"\nSection: {sec}", bold=True) + f"  file: {out_path}")
        click.echo("".join(diff_lines))

        action = click.prompt(
            "Action",
            type=click.Choice(["o", "a", "s"], case_sensitive=False),
            default="s",
            show_choices=False,
            prompt_suffix=" [o=override / a=append / s=skip] > ",
        )

        if action.lower() == "s":
            click.echo("  → skipped.")
        elif action.lower() == "o":
            out_path.write_text(device_yaml, encoding="utf-8")
            click.echo(click.style("  override", fg="yellow") + f"  {out_path}")
        else:  # "a"
            existing_data = yaml.safe_load(existing_text) or {}
            merged = _merge_section(sec, existing_data, device_data)
            merged_yaml = yaml.dump(
                merged, default_flow_style=False, allow_unicode=True, sort_keys=False
            )
            out_path.write_text(merged_yaml, encoding="utf-8")
            click.echo(click.style("  append", fg="cyan") + f"  {out_path}")

    # Generate configuration.yaml when writing to a fresh directory
    cfg_out = out_dir / "configuration.yaml"
    if created_sections and not cfg_out.exists():
        target_cfg = cfg.targets[target]
        includes = "\n".join(
            f"{sec}:     !include {_SECTION_FILENAME[sec]}"
            for sec in ["dhcp", "nat", "firewall"]
            if sec in created_sections
        )
        cfg_content = (
            'version: "1.0"\n\n'
            "targets:\n"
            f"  {target}:\n"
            f"    type: {target_cfg.type.value}\n"
            f"    host: !secret {target}_host\n"
        )
        if target_cfg.type.value == "bbox_ultim":
            cfg_content += f"    password: !secret {target}_password\n"
        else:
            cfg_content += f"    ssh_user: {target_cfg.ssh_user}\n"
            cfg_content += f"    ssh_key: !secret {target}_ssh_key\n"
            cfg_content += f"    ssh_port: {target_cfg.ssh_port}\n"
        cfg_content += f"\n{includes}\n"
        cfg_out.write_text(cfg_content, encoding="utf-8")
        click.echo(click.style("  create", fg="green") + f"  {cfg_out}")
