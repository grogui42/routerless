"""Custom YAML loader supporting !include, !include_dir_merge_list,
!include_dir_named, and !secret tags — inspired by Home Assistant's
configuration loader.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


class SecretNotFoundError(Exception):
    """Raised when a !secret key cannot be resolved."""


class _Loader(yaml.SafeLoader):
    """YAML loader with support for custom tags.

    Each instance is bound to a specific file path so that relative
    !include paths and !secret resolution are anchored to the correct
    directory. The config_root is the top-level configuration.yaml and
    sets the upper boundary for !secret resolution.
    """

    def __init__(self, stream: Any, file_path: Path, config_root: Path) -> None:
        super().__init__(stream)
        self._file_path = file_path
        self._dir = file_path.parent
        self._config_root = config_root  # always the top-level config file

    # ------------------------------------------------------------------
    # !include <relative_path>
    # ------------------------------------------------------------------
    def include(self, node: yaml.Node) -> Any:
        rel = self.construct_scalar(node)  # type: ignore[arg-type]
        target = (self._dir / rel).resolve()
        return _load_file(target, self._config_root)

    # ------------------------------------------------------------------
    # !include_dir_merge_list <relative_dir>
    # ------------------------------------------------------------------
    def include_dir_merge_list(self, node: yaml.Node) -> list[Any]:
        rel = self.construct_scalar(node)  # type: ignore[arg-type]
        target_dir = (self._dir / rel).resolve()
        if not target_dir.is_dir():
            raise FileNotFoundError(
                f"!include_dir_merge_list: directory not found: {target_dir}"
            )
        result: list[Any] = []
        for yaml_file in sorted(target_dir.glob("*.yaml")):
            content = _load_file(yaml_file, self._config_root)
            if not isinstance(content, list):
                raise TypeError(
                    f"!include_dir_merge_list: '{yaml_file}' must contain a YAML list,"
                    f" got {type(content).__name__}"
                )
            result.extend(content)
        return result

    # ------------------------------------------------------------------
    # !include_dir_named <relative_dir>
    # ------------------------------------------------------------------
    def include_dir_named(self, node: yaml.Node) -> dict[str, Any]:
        rel = self.construct_scalar(node)  # type: ignore[arg-type]
        target_dir = (self._dir / rel).resolve()
        if not target_dir.is_dir():
            raise FileNotFoundError(
                f"!include_dir_named: directory not found: {target_dir}"
            )
        return {
            yaml_file.stem: _load_file(yaml_file, self._config_root)
            for yaml_file in sorted(target_dir.glob("*.yaml"))
        }

    # ------------------------------------------------------------------
    # !secret <key>
    # ------------------------------------------------------------------
    def secret(self, node: yaml.Node) -> Any:
        key = self.construct_scalar(node)  # type: ignore[arg-type]
        secrets = _resolve_secrets(self._dir, self._config_root)
        if key not in secrets:
            raise SecretNotFoundError(
                f"Secret '{key}' not found. "
                f"Add it to a 'secrets.yaml' file in '{self._dir}' or a parent directory."
            )
        return secrets[key]


# Register constructors on the class (not on instances)
_Loader.add_constructor("!include", _Loader.include)
_Loader.add_constructor("!include_dir_merge_list", _Loader.include_dir_merge_list)
_Loader.add_constructor("!include_dir_named", _Loader.include_dir_named)
_Loader.add_constructor("!secret", _Loader.secret)


def _load_file(path: Path, config_root: Path) -> Any:
    """Load a single YAML file with the custom loader."""
    path = path.resolve()
    with open(path, encoding="utf-8") as f:
        loader = _Loader(f, path, config_root)
        try:
            return loader.get_single_data()
        finally:
            loader.dispose()


def _resolve_secrets(start_dir: Path, config_root_file: Path) -> dict[str, Any]:
    """Walk from start_dir up to the directory containing config_root_file,
    collecting the first secrets.yaml found. Never escapes the config root.
    """
    config_root_dir = config_root_file.parent.resolve()
    current = start_dir.resolve()

    while True:
        candidate = current / "secrets.yaml"
        if candidate.is_file():
            with open(candidate, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return data

        if current == config_root_dir:
            break
        parent = current.parent
        if parent == current:
            # Filesystem root reached without finding config root
            break
        current = parent

    return {}


def load_config(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load and resolve a routerless configuration file.

    Supports !include, !include_dir_merge_list, !include_dir_named and !secret.
    The file must be a YAML mapping at the top level.

    Args:
        path: Path to the root configuration file (e.g. ``configuration.yaml``).

    Returns:
        The fully-resolved configuration as a plain Python dict.

    Raises:
        FileNotFoundError: If the config file or an !include target doesn't exist.
        SecretNotFoundError: If a !secret key cannot be resolved.
        TypeError: If an !include_dir_merge_list file doesn't contain a list.
    """
    file_path = Path(path).resolve()
    if not file_path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {file_path}")
    result = _load_file(file_path, config_root=file_path)
    if not isinstance(result, dict):
        raise TypeError(
            f"Top-level configuration must be a YAML mapping, got {type(result).__name__}"
        )
    return result
