---
mode: agent
description: "Capture Bbox XHR endpoints via mitmproxy to implement unknown API features (firewall, etc.)"
---

# Discover Bbox API Endpoints via mitmproxy

This prompt guides you through setting up a local HTTPS proxy to intercept Bbox web-interface XHR traffic and identify undocumented REST endpoints.

## Prerequisites

Install mitmproxy in the project virtualenv:

```bash
pip install mitmproxy
```

## Step 1 — Start the intercepting proxy

Run this script to start mitmproxy and log all Bbox API calls:

```python
# scripts/bbox_intercept.py
"""mitmproxy addon: log all Bbox API requests to bbox_captured.jsonl"""
import json
from pathlib import Path
from mitmproxy import http

LOG_FILE = Path("bbox_captured.jsonl")
BBOX_HOST = "mabbox.bytel.fr"


class BboxCapture:
    def request(self, flow: http.HTTPFlow) -> None:
        if BBOX_HOST not in flow.request.pretty_host:
            return
        if "/api/v1/" not in flow.request.path:
            return
        entry = {
            "method": flow.request.method,
            "path": flow.request.path,
            "query": dict(flow.request.query),
            "content_type": flow.request.headers.get("content-type", ""),
            "body": flow.request.text,
        }
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        print(f"[bbox] {entry['method']} {entry['path']}  body={entry['body'][:120]}")

    def response(self, flow: http.HTTPFlow) -> None:
        if BBOX_HOST not in flow.request.pretty_host:
            return
        if "/api/v1/" not in flow.request.path:
            return
        entry = {
            "path": flow.request.path,
            "status": flow.response.status_code,
            "response_body": flow.response.text[:500],
        }
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


addons = [BboxCapture()]
```

Launch it:

```bash
mitmdump -s scripts/bbox_intercept.py --listen-port 8080 --ssl-insecure
```

## Step 2 — Route browser traffic through the proxy

**Firefox / Chrome:** Set HTTP proxy to `127.0.0.1:8080`, then visit `http://mitm.it` and install the mitmproxy CA certificate.

**Or use curl for targeted probing:**

```bash
# All requests via the proxy, trusting mitmproxy CA
export https_proxy=http://127.0.0.1:8080
curl -k https://mabbox.bytel.fr/api/v1/firewall/rules
```

## Step 3 — Trigger the feature in the Bbox UI

1. Open `http://mabbox.bytel.fr` in the proxied browser
2. Log in, navigate to the relevant section (e.g. Firewall → Rules)
3. Add, modify, or delete a rule to trigger POST/PUT/DELETE requests
4. Watch the mitmproxy terminal output and `bbox_captured.jsonl`

## Step 4 — Analyse captured requests

```python
# scripts/analyse_capture.py
import json
from pathlib import Path

for line in Path("bbox_captured.jsonl").read_text().splitlines():
    e = json.loads(line)
    if "method" in e and e["method"] in ("POST", "PUT", "DELETE", "PATCH"):
        print(f"{e['method']:6} {e['path']}")
        if e["body"]:
            print(f"       body: {e['body'][:200]}")
```

## Step 5 — Implement the discovered endpoint

Once you have confirmed the endpoint, implement it in `routerless/adapters/bbox_ultim.py`:

1. Add a private `_list_<feature>()`, `_create_<feature>()`, `_delete_<feature>()` method
2. Implement `apply_<feature>(config)` following the same declarative-sync pattern as `apply_dhcp()`
3. Remove the `NotImplementedError` from the existing stub
4. Add the endpoint details to the `AGENTS.md` confirmed endpoints list
5. Add tests in `tests/test_bbox.py` using `_mock_http` + `_bbox_resp`

## Current stubs needing discovery

| Method | Status | Notes |
|--------|--------|-------|
| `apply_firewall()` | `NotImplementedError` | Guessed: `GET/POST/DELETE /firewall/rules` |

## Safety

- **Never use this proxy on a shared/production network** — it is a MITM proxy
- The script only logs requests to `mabbox.bytel.fr` to minimise exposure
- Captured files may contain session cookies — delete `bbox_captured.jsonl` after analysis
- The Bbox rate-limits login: 3 failures → 300–1200 s lockout; do not probe `/login` repeatedly
