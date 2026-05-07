# Contributing to routerless

Thank you for taking the time to contribute! This document covers everything you need to know to get started.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Workflow](#workflow)
- [Code Style](#code-style)
- [Tests](#tests)
- [Commit Messages](#commit-messages)
- [Adding a New Router Adapter](#adding-a-new-router-adapter)
- [Adding a New CLI Feature](#adding-a-new-cli-feature)

---

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).
By participating you agree to uphold it. Please report unacceptable behaviour to the repository maintainers.

---

## Getting Started

```bash
git clone https://github.com/you/routerless
cd routerless
pip install -e ".[dev]"
pytest   # must be green before you start
```

Python 3.13+ is required.

---

## Workflow

1. **Fork** the repository and create a feature branch from `main`:
   ```bash
   git checkout -b feat/my-feature
   ```
2. **Make your changes.** Keep each PR focused on a single concern.
3. **Add or update tests.** Every change that touches adapter or CLI logic must be covered.
   Run `pytest` — all tests must stay green.
4. **Open a Pull Request** against `main`.
   - Describe *what* changed and *why*.
   - Reference any related issues with `Fixes #123` or `Closes #123`.
   - Keep the PR description concise; let the code speak.

---

## Code Style

- **Python 3.13+** — use modern syntax: `str | None`, `list[...]`, `dict[...]`.
  Do not use `Optional`, `List`, or `Dict` from `typing`.
- **Pydantic v2** — use `model_dump(mode="json", exclude_none=True, exclude_unset=True)` for serialisation.
- **No docstrings, comments, or type annotations** on code you didn't change.
- **No `print()`** — use `click.echo()` for all user-facing output.
- **Secrets must never be logged** or included in error messages.
- Apply only what is requested — don't add features, refactors, or "improvements" beyond what was asked.

---

## Tests

All device I/O must be mocked — **never make real network calls in tests**.

| Test file | What it covers |
|-----------|---------------|
| `tests/test_bbox.py` | Bbox Ultim adapter — HTTP mocked via `unittest.mock` |
| `tests/test_openwrt.py` | OpenWrt adapter — SSH mocked |
| `tests/test_plan.py` | `plan` helpers and CLI integration |
| `tests/test_export.py` | `export` helpers and CLI integration |
| `tests/test_init.py` | `init` command |

CLI tests use `click.testing.CliRunner` with `_load` and `_get_adapter` patched.
See `tests/test_export.py` for a complete pattern to follow.

Run the full suite before opening a PR:

```bash
pytest --tb=short -q
```

---

## Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add export command
fix: resolve config path for non-existent directories
test: add stale redirect deletion tests for OpenWrt
docs: update README with contributing guide
refactor: extract _get_existing_redirects helper
```

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`.

---

## Adding a New Router Adapter

Use the built-in prompt in VS Code with GitHub Copilot:

```
# reference .github/prompts/add-adapter.prompt.md
```

Or follow these manual steps:

1. Add a `TargetType` enum value in `routerless/models/config.py`.
2. Create `routerless/adapters/my_router.py` extending `BaseAdapter`.
   Implement all four abstract methods: `apply_dhcp`, `apply_nat`, `apply_firewall`, `dump`.
3. Register it in `_ADAPTER_MAP` in `routerless/cli.py`.
4. Add tests in `tests/test_my_router.py` — mock all I/O.
5. Update `AGENTS.md` with any confirmed endpoints or adapter-specific behaviour.

---

## Adding a New CLI Feature

Use the built-in prompt:

```
# reference .github/prompts/add-feature.prompt.md
```

The general pattern:

1. Add a method stub to `BaseAdapter` (raise `NotImplementedError`).
2. Implement it in each adapter.
3. Add the Click command in `cli.py`.
4. Write tests (both unit and CLI integration).
5. Update `README.md` with usage examples.
