# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest `main` | ✅ |

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report security issues by emailing the maintainers directly (see the GitHub repository contact).
Include as much detail as possible:

- Description of the vulnerability
- Steps to reproduce
- Potential impact

You will receive a response within 72 hours. If the issue is confirmed, a fix will be prioritised and a new release made as soon as possible.

## Security Notes

- `secrets.yaml` is **never committed** — it is listed in `.gitignore` by default.
  Do not commit credentials under any name.
- The Bbox adapter enforces a rate-limit policy: **3 failed logins → up to 1200 s lockout**.
  Do not iterate over passwords.
- SSH host key verification uses `RejectPolicy` (paramiko) — unknown hosts are rejected, not auto-accepted.
- All mutating Bbox API requests require a CSRF btoken fetched after login.
  The token is never logged.
