# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.1] - 2026-07-08

### Fixed

- Freebox certificate bundle and `VERIFY_X509_STRICT` handling for Python 3.13 compatibility
- Added `--disable-ssl-verify` option to `get_freebox_app_token` utility script
- Retrieve Freebox gateway and subnet from DHCP config instead of hardcoded values
- GitHub CI badge URL

## [0.2.0] - 2026-05-12

### Added

- **Freebox Router Adapter** — New adapter for Freebox routers with full support for:
  - Static DHCP reservations (`GET/POST/PUT/DELETE /dhcp/static_lease/`)
  - Port forwarding rules (NAT) (`GET/POST/PUT/DELETE /fw/redir/`)
  - HMAC-SHA1 challenge-response authentication
  - Self-signed certificate validation with embedded Root CA certificates
- **Freebox app_token Utility Script** — OAuth-based app token retrieval for Freebox authentication (`routerless/scripts/get_freebox_app_token.py`)
- **Freebox Templates** — init command now includes Freebox router configuration template

### Changed

- Updated README and contributing prompts with Freebox adapter documentation

### Fixed

- Embedded Freebox Root CA certificates (RSA and ECDSA) for SSL verification without external dependencies
- Resolved linting issues (unused imports, variables, and whitespace)

### Supported Devices

- Bbox Ultim (existing)
- Freebox Router (new in 0.2.0)
- OpenWrt (existing)
- QNAP Qhora 301W (existing)

## [0.1.0] - 2026-05-08

### Added

- Initial release
- **Adapters:**
  - Bbox Ultim (HTTPS REST + cookie auth)
  - OpenWrt (SSH + UCI commands)
  - QNAP Qhora 301W (OpenWrt-based)
- **Features:**
  - Declarative YAML config for router-agnostic network management
  - DHCP reservation management
  - NAT/port forwarding rules
  - Firewall rules support (Bbox Ultim only)
  - WiFi on/off control (Bbox Ultim only)
- **CLI Commands:**
  - `routerless validate` — validate config syntax and schema
  - `routerless apply` — apply config to target router
  - `routerless status` — show router network status
  - `routerless dump` — export current router state as YAML
  - `routerless diff` — preview changes before applying
  - `routerless devices` — list managed devices
  - `routerless wifi` — control WiFi (Bbox Ultim only)
  - `routerless init` — scaffold new configuration
- **Config System:**
  - Custom YAML tags: `!include`, `!include_dir_merge_list`, `!include_dir_named`, `!secret`
  - Modular configuration with included files
  - Secrets management via `secrets.yaml`

### Documentation

- Comprehensive README
- Contributing guidelines with release process
- Code of Conduct
- Security policy

[Unreleased]: https://github.com/grogui42/routerless/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/grogui42/routerless/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/grogui42/routerless/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/grogui42/routerless/releases/tag/v0.1.0
