---
mode: agent
description: "Add a new CLI command, option, or feature and implement it on all configured adapters"
---

# Add a New Feature / CLI Command

Use this prompt to add a new capability to routerless — a new command, a new option on an existing command, or a new section type — and wire it up across all adapters.

## What you need to provide

- **Feature description**: what the command does (e.g. "reboot router", "show bandwidth stats", "backup config")
- **CLI shape**: command name, options, arguments (e.g. `routerless reboot --target bbox`)
- **Scope**: which adapters should implement it (all / bbox only / openwrt only)

---

## Architecture reminder

```
CLI command (cli.py)
  └── adapter.new_method()          ← defined in BaseAdapter (raises NotImplementedError)
        ├── BboxUltimAdapter        ← implements via HTTPS REST
        ├── OpenWrtAdapter          ← implements via SSH + UCI / shell commands
        └── QnapQhoraAdapter        ← delegates to self._openwrt.new_method()
```

If the feature returns structured data, put its dataclass in `routerless/models/status.py`.

---

## Step-by-step

### 1. Define the data model (if needed)

In `routerless/models/status.py`, add a dataclass for the return value:

```python
@dataclass
class MyFeatureResult:
    field_a: str
    field_b: int
    optional_c: str = ""
```

### 2. Add the method to `BaseAdapter`

In `routerless/adapters/base.py`, add a concrete method with a clear `NotImplementedError`:

```python
def my_feature(self, param: str) -> MyFeatureResult:
    raise NotImplementedError(
        f"{type(self).__name__} does not implement my_feature()."
    )
```

Import `MyFeatureResult` at the top of `base.py` alongside `AdapterStatus` etc.

### 3. Implement in each adapter

**BboxUltimAdapter** (`bbox_ultim.py`) — REST pattern:
```python
def my_feature(self, param: str) -> MyFeatureResult:
    with self._make_client() as client:
        self._login(client)
        try:
            data = self._get(client, "/some/endpoint")
        finally:
            self._logout(client)
    # parse data → return MyFeatureResult(...)
```

**OpenWrtAdapter** (`openwrt.py`) — SSH pattern:
```python
def my_feature(self, param: str) -> MyFeatureResult:
    with self._ssh() as client:
        out = self._run(client, "some-command")
    # parse out → return MyFeatureResult(...)
```

**QnapQhoraAdapter** (`qnap_qhora.py`) — delegate:
```python
def my_feature(self, param: str) -> MyFeatureResult:
    self._assert_uci_available()
    return self._openwrt.my_feature(param)
```

### 4. Add the CLI command in `cli.py`

```python
@cli.command("my-feature")
@click.argument("config", default="configuration.yaml", type=click.Path(exists=True))
@click.option("--target", "-t", required=True, help="Target name")
@click.option("--param", default="", help="Description of param")
def cmd_my_feature(config: str, target: str, param: str) -> None:
    """One-line description shown in --help."""
    cfg = _load(config)
    adapter = _get_adapter(cfg, target)
    try:
        result = adapter.my_feature(param)
    except NotImplementedError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Result: {result.field_a}  ({result.field_b})")
```

If it belongs in a group (e.g. `wifi on/off`), use `@cli.group` + `@grp.command`.

### 5. Write tests

**For Bbox** (`tests/test_bbox.py`) — mock HTTP:
```python
class TestMyFeature:
    def test_returns_result(self) -> None:
        adapter = _adapter()
        mock_client = MagicMock()
        mock_client.get.return_value = _bbox_resp([{"section": {"key": "value"}}])
        p_make, p_login, p_logout = _mock_http(adapter, mock_client)
        with p_make, p_login, p_logout:
            result = adapter.my_feature("param")
        assert result.field_a == "value"
```

**For OpenWrt** (`tests/test_openwrt.py`) — mock SSH:
```python
class TestMyFeature:
    def test_returns_result(self) -> None:
        adapter = _make_adapter()
        mock_client = _mock_ssh_client({"some-command": b"output"})
        with patch.object(adapter, "_ssh", return_value=_ssh_ctx(mock_client)):
            result = adapter.my_feature("param")
        assert result.field_b == 42
```

### 6. Validate

```bash
pytest                    # all tests green
routerless my-feature --target bbox config/configuration.yaml   # live smoke test
routerless my-feature --target openwrt config/configuration.yaml
```

---

## Notes

- Features that only one adapter supports: implement in that adapter, let `BaseAdapter.my_feature()` raise `NotImplementedError` — the CLI already handles it gracefully.
- New **section types** (beyond dhcp/nat/firewall): also add the Pydantic model to `routerless/models/config.py`, add the field to `NetworkConfig`, and add `apply_<section>` to `BaseAdapter` as an abstract method.
- Keep `AGENTS.md` updated with any new confirmed endpoints or adapter-specific behaviour.
