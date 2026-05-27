#!/usr/bin/env python3
"""Utility to obtain a Freebox app_token for routerless.

This script helps users obtain the app_token required for the Freebox router adapter.
It performs the authorization flow, prompting the user to press a button on their
Freebox to grant access to routerless.

Usage:
    python -m routerless.scripts.get_freebox_app_token [-h] [--disable-ssl-verify]
    # or
    python routerless/scripts/get_freebox_app_token.py [-h] [--disable-ssl-verify]

Options:
    -h, --help            show this help message and exit
    --disable-ssl-verify  Disable SSL verification checks

The script will:
1. Request authorization from the Freebox
2. Display a PIN and ask you to grant application on Freebox device
   (press the WiFi button or accept using buttons depending on the model)
3. Poll for authorization status
4. Return the app_token to store in secrets.yaml

See: https://dev.freebox.fr/sdk/os/
"""
from __future__ import annotations

import argparse
import ssl
import sys
import time
from typing import Any

import httpx

from routerless.certificates import FREEBOX_CA_BUNDLE

_DEFAULT_TIMEOUT = 10.0
_BASE_URL = "https://mafreebox.freebox.fr/api/v4"

# These must match the values in freebox_router.py
_APP_ID = "fr.freebox.routerless"
_APP_NAME = "Routerless"
_APP_VERSION = "1.0"
_DEVICE_NAME = "Routerless CLI"

# Authorization polling constants
_POLL_INTERVAL = 1.0  # seconds
_POLL_MAX_ATTEMPTS = 120  # ~2 minutes


def main() -> int:
    """Execute the Freebox app_token authorization flow."""
    print(f"Freebox App Token Retrieval for {_APP_NAME}")
    print("=" * 60)
    print()

    try:
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--disable-ssl-verify",
            action="store_true",
            help="Disable SSL verification checks"
        )
        args = parser.parse_args()

        # Determine SSL verification
        if args.disable_ssl_verify:
            verify = False
        else:
            ssl_ctx = ssl.create_default_context()  # NOSONAR Python 3.13+ so secure
            ssl_ctx.load_verify_locations(str(FREEBOX_CA_BUNDLE))  # Use embedded Root CA bundle
            # Disable strict validating introduced in Python 3.13, which doesn't work with default Freebox certificates
            ssl_ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
            verify = ssl_ctx

        with httpx.Client(
            base_url=_BASE_URL,
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=True,
            verify=verify
        ) as client:
            # Step 1: Request authorization
            print("Step 1: Requesting authorization from Freebox...")
            auth_response = _request_authorization(client)
            track_id = auth_response.get("track_id")
            app_token = auth_response.get("app_token")
            status = auth_response.get("status")

            if not track_id:
                print("❌ Error: No track_id in authorization response")
                return 1

            print(f"✓ Authorization request sent (track_id: {track_id})")
            print()

            # Step 2: Display PIN and wait for user action
            print("Step 2: User action required")
            print("-" * 60)
            if status == "pending":
                print("⏳ Waiting for authorization...")
                print("Please press the WiFi button on your Freebox.")
                print()

            # Step 3: Poll for authorization
            print("Step 3: Polling for authorization status...")
            status = _poll_authorization(client, track_id)

            if status == "granted":
                print("✓ Authorization granted!")
                print()
                print("=" * 60)
                print("SUCCESS! Here is your app_token:")
                print("=" * 60)
                print(app_token)
                print("=" * 60)
                print()
                print("Store this in your secrets.yaml file:")
                print("""
targets:
  freebox:
    type: freebox
    host: mafreebox.freebox.fr
    password: <paste_the_app_token_here>
                """)
                return 0
            else:
                print(f"❌ Authorization failed or timed out (status: {status})")
                return 1

    except httpx.HTTPError as e:
        print(f"❌ HTTP Error: {e}")
        return 1
    except Exception as e:
        print(f"❌ Error: {e}")
        return 1


def _request_authorization(client: httpx.Client) -> dict[str, Any]:
    """Request authorization from the Freebox.

    Returns a dict with track_id, app_token, and status.
    """
    auth_data = {
        "app_id": _APP_ID,
        "app_name": _APP_NAME,
        "app_version": _APP_VERSION,
        "device_name": _DEVICE_NAME,
    }

    resp = client.post("/login/authorize/", json=auth_data)
    resp.raise_for_status()
    data = resp.json()

    if not data.get("success"):
        raise RuntimeError(
            f"Authorization request failed: {data.get('msg', 'unknown error')}"
        )

    return data.get("result", {})


def _poll_authorization(client: httpx.Client, track_id: str) -> str:
    """Poll the authorization endpoint until user grants or times out.

    Returns (app_token, status).
    """
    for attempt in range(_POLL_MAX_ATTEMPTS):
        resp = client.get(f"/login/authorize/{track_id}")
        resp.raise_for_status()
        data = resp.json()

        if not data.get("success"):
            raise RuntimeError(
                f"Authorization poll failed: {data.get('msg', 'unknown error')}"
            )

        result = data.get("result", {})
        status = result.get("status")

        if status == "granted":
            return status
        elif status == "denied":
            raise RuntimeError("User denied authorization on the Freebox device")
        elif status == "timeout":
            raise RuntimeError("Authorization request timed out on the Freebox")

        # Still pending, wait and retry
        elapsed = (attempt + 1) * _POLL_INTERVAL
        print(f"  [{elapsed:.0f}s] Status: {status}...", end="\r")
        time.sleep(_POLL_INTERVAL)

    raise RuntimeError("Authorization polling timed out (exceeded max attempts)")


if __name__ == "__main__":
    sys.exit(main())
