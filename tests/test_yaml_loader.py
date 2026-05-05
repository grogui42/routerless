"""Tests for the custom YAML loader."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from routerless.yaml_loader import SecretNotFoundError, load_config


def _write(path: Path, content: str) -> Path:
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# !include
# ---------------------------------------------------------------------------

class TestInclude:
    def test_include_scalar(self, tmp_path: Path) -> None:
        _write(tmp_path / "sub.yaml", "value: hello\n")
        root = _write(tmp_path / "configuration.yaml", """\
            data: !include sub.yaml
        """)
        cfg = load_config(root)
        assert cfg["data"] == {"value": "hello"}

    def test_include_list(self, tmp_path: Path) -> None:
        _write(tmp_path / "items.yaml", "- a\n- b\n")
        root = _write(tmp_path / "configuration.yaml", "items: !include items.yaml\n")
        cfg = load_config(root)
        assert cfg["items"] == ["a", "b"]

    def test_include_nested(self, tmp_path: Path) -> None:
        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        _write(sub_dir / "leaf.yaml", "x: 1\n")
        _write(sub_dir / "mid.yaml", "leaf: !include leaf.yaml\n")
        root = _write(tmp_path / "configuration.yaml", "mid: !include sub/mid.yaml\n")
        cfg = load_config(root)
        assert cfg["mid"]["leaf"] == {"x": 1}

    def test_include_missing_file(self, tmp_path: Path) -> None:
        root = _write(tmp_path / "configuration.yaml", "data: !include missing.yaml\n")
        with pytest.raises(FileNotFoundError):
            load_config(root)


# ---------------------------------------------------------------------------
# !include_dir_merge_list
# ---------------------------------------------------------------------------

class TestIncludeDirMergeList:
    def test_merge_list(self, tmp_path: Path) -> None:
        d = tmp_path / "items"
        d.mkdir()
        _write(d / "a.yaml", "- {name: A}\n")
        _write(d / "b.yaml", "- {name: B}\n- {name: C}\n")
        root = _write(tmp_path / "configuration.yaml", "things: !include_dir_merge_list items\n")
        cfg = load_config(root)
        assert cfg["things"] == [{"name": "A"}, {"name": "B"}, {"name": "C"}]

    def test_merge_list_sorted(self, tmp_path: Path) -> None:
        d = tmp_path / "items"
        d.mkdir()
        _write(d / "z.yaml", "- z\n")
        _write(d / "a.yaml", "- a\n")
        root = _write(tmp_path / "configuration.yaml", "items: !include_dir_merge_list items\n")
        cfg = load_config(root)
        assert cfg["items"] == ["a", "z"]

    def test_merge_list_non_list_raises(self, tmp_path: Path) -> None:
        d = tmp_path / "items"
        d.mkdir()
        _write(d / "bad.yaml", "key: value\n")
        root = _write(tmp_path / "configuration.yaml", "items: !include_dir_merge_list items\n")
        with pytest.raises(TypeError, match="must contain a YAML list"):
            load_config(root)

    def test_merge_list_missing_dir_raises(self, tmp_path: Path) -> None:
        root = _write(tmp_path / "configuration.yaml", "items: !include_dir_merge_list no_such_dir\n")
        with pytest.raises(FileNotFoundError):
            load_config(root)


# ---------------------------------------------------------------------------
# !secret
# ---------------------------------------------------------------------------

class TestSecret:
    def test_secret_same_dir(self, tmp_path: Path) -> None:
        _write(tmp_path / "secrets.yaml", "my_pass: s3cr3t\n")
        root = _write(tmp_path / "configuration.yaml", "password: !secret my_pass\n")
        cfg = load_config(root)
        assert cfg["password"] == "s3cr3t"

    def test_secret_in_included_file_resolves_from_parent(self, tmp_path: Path) -> None:
        _write(tmp_path / "secrets.yaml", "api_key: abc123\n")
        sub = tmp_path / "sub"
        sub.mkdir()
        _write(sub / "child.yaml", "key: !secret api_key\n")
        root = _write(tmp_path / "configuration.yaml", "child: !include sub/child.yaml\n")
        cfg = load_config(root)
        assert cfg["child"]["key"] == "abc123"

    def test_secret_missing_raises(self, tmp_path: Path) -> None:
        _write(tmp_path / "secrets.yaml", "other: value\n")
        root = _write(tmp_path / "configuration.yaml", "x: !secret no_such_key\n")
        with pytest.raises(SecretNotFoundError, match="no_such_key"):
            load_config(root)

    def test_secret_no_secrets_file_raises(self, tmp_path: Path) -> None:
        root = _write(tmp_path / "configuration.yaml", "x: !secret anything\n")
        with pytest.raises(SecretNotFoundError):
            load_config(root)

    def test_secret_does_not_escape_config_root(self, tmp_path: Path) -> None:
        # Place a secrets.yaml one level above the config root — should not be found
        _write(tmp_path / "secrets.yaml", "rogue: leaked\n")
        config_dir = tmp_path / "myconfig"
        config_dir.mkdir()
        root = _write(config_dir / "configuration.yaml", "x: !secret rogue\n")
        with pytest.raises(SecretNotFoundError):
            load_config(root)


# ---------------------------------------------------------------------------
# load_config top-level errors
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.yaml")

    def test_non_mapping_root_raises(self, tmp_path: Path) -> None:
        root = _write(tmp_path / "configuration.yaml", "- item1\n- item2\n")
        with pytest.raises(TypeError, match="mapping"):
            load_config(root)
