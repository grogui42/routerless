"""Tests for the `init` command."""
from __future__ import annotations

from click.testing import CliRunner

from routerless.cli import cli


class TestCmdInit:
    def test_creates_all_files(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["init", "."])
            assert result.exit_code == 0, result.output
            from pathlib import Path
            assert Path("configuration.yaml").exists()
            assert Path("secrets.yaml.example").exists()
            assert Path("dhcp.yaml").exists()
            assert Path("nat.yaml").exists()
            assert Path("firewall.yaml").exists()
            assert Path(".gitignore").exists()

    def test_output_shows_created_files(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["init", "."])
            assert "create" in result.output
            assert "configuration.yaml" in result.output
            assert "secrets.yaml.example" in result.output

    def test_shows_next_steps(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["init", "."])
            assert "Next steps" in result.output
            assert "secrets.yaml" in result.output
            assert "validate" in result.output

    def test_skips_existing_files_without_force(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(cli, ["init", "."])
            # Write a sentinel value to configuration.yaml
            from pathlib import Path
            Path("configuration.yaml").write_text("SENTINEL", encoding="utf-8")
            runner.invoke(cli, ["init", "."])
            # File must NOT have been overwritten
            assert Path("configuration.yaml").read_text() == "SENTINEL"

    def test_skip_message_when_file_exists(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(cli, ["init", "."])
            result = runner.invoke(cli, ["init", "."])
            assert "skip" in result.output

    def test_force_overwrites_existing_files(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(cli, ["init", "."])
            from pathlib import Path
            Path("configuration.yaml").write_text("SENTINEL", encoding="utf-8")
            runner.invoke(cli, ["init", ".", "--force"])
            content = Path("configuration.yaml").read_text()
            assert "SENTINEL" not in content
            assert "version:" in content

    def test_creates_nested_directory(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["init", "my/network/config"])
            assert result.exit_code == 0
            from pathlib import Path
            assert Path("my/network/config/configuration.yaml").exists()

    def test_configuration_yaml_is_valid(self) -> None:
        """Generated configuration.yaml must parse without error when secrets exist."""
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(cli, ["init", "."])
            from pathlib import Path
            # Create a minimal secrets.yaml so !secret resolves
            Path("secrets.yaml").write_text(
                "bbox_host: '192.168.1.254'\nbbox_password: 'test'\n",
                encoding="utf-8",
            )
            result = runner.invoke(cli, ["validate", "configuration.yaml"])
            assert result.exit_code == 0, result.output

    def test_gitignore_contains_secrets(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(cli, ["init", "."])
            from pathlib import Path
            gitignore = Path(".gitignore").read_text()
            assert "secrets.yaml" in gitignore

    def test_nothing_written_message_when_all_exist(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            runner.invoke(cli, ["init", "."])
            result = runner.invoke(cli, ["init", "."])
            assert "Nothing written" in result.output
