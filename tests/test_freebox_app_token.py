"""Tests for Freebox app_token retrieval script."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import sys
import pytest

from routerless.scripts.get_freebox_app_token import (
    _APP_ID,
    _APP_NAME,
    _APP_VERSION,
    _DEVICE_NAME,
    _poll_authorization,
    _request_authorization,
    main,
)


def test_request_authorization_success() -> None:
    """Test successful authorization request."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "success": True,
        "result": {
            "track_id": "abc123",
            "app_token": "token_value",
            "status": "pending",
        },
    }
    mock_client.post.return_value = mock_response

    result = _request_authorization(mock_client)

    assert result["track_id"] == "abc123"
    assert result["app_token"] == "token_value"
    assert result["status"] == "pending"

    # Verify the request was made with correct data
    mock_client.post.assert_called_once_with(
        "/login/authorize/",
        json={
            "app_id": _APP_ID,
            "app_name": _APP_NAME,
            "app_version": _APP_VERSION,
            "device_name": _DEVICE_NAME,
        },
    )


def test_request_authorization_failure() -> None:
    """Test authorization request failure."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "success": False,
        "msg": "Invalid request",
    }
    mock_client.post.return_value = mock_response

    with pytest.raises(RuntimeError, match="Authorization request failed"):
        _request_authorization(mock_client)


def test_poll_authorization_granted() -> None:
    """Test polling when authorization is granted."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "success": True,
        "result": {
            "status": "granted",
            "challenge": "xxx",
            "password_salt": "yyy"  # NOSONAR : Test data
        },
    }
    mock_client.get.return_value = mock_response

    status = _poll_authorization(mock_client, "track_id_123")

    assert status == "granted"


def test_poll_authorization_denied() -> None:
    """Test polling when authorization is denied."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "success": True,
        "result": {
            "status": "denied",
            "app_token": "",
        },
    }
    mock_client.get.return_value = mock_response

    with pytest.raises(RuntimeError, match="User denied authorization"):
        _poll_authorization(mock_client, "track_id_123")


def test_poll_authorization_timeout() -> None:
    """Test polling when authorization request times out."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "success": True,
        "result": {
            "status": "timeout",
            "app_token": "",
        },
    }
    mock_client.get.return_value = mock_response

    with pytest.raises(RuntimeError, match="Authorization request timed out"):
        _poll_authorization(mock_client, "track_id_123")


def test_poll_authorization_pending_then_granted() -> None:
    """Test polling when status transitions from pending to granted."""
    mock_client = MagicMock()

    # First call returns pending
    pending_response = MagicMock()
    pending_response.json.return_value = {
        "success": True,
        "result": {
            "status": "pending",
            "challenge": "xxx",
            "password_salt": "yyy"  # NOSONAR : Test data
        },
    }

    # Second call returns granted
    granted_response = MagicMock()
    granted_response.json.return_value = {
        "success": True,
        "result": {
            "status": "granted",
            "challenge": "xxx",
            "password_salt": "yyy"  # NOSONAR : Test data
        },
    }

    mock_client.get.side_effect = [pending_response, granted_response]

    with patch("time.sleep"):  # Mock sleep to avoid delays in tests
        status = _poll_authorization(mock_client, "track_id_123")

    assert status == "granted"
    assert mock_client.get.call_count == 2


def test_main_success() -> None:
    """Test main function with successful authorization flow."""
    with patch("routerless.scripts.get_freebox_app_token.httpx.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        # Mock the authorization request
        auth_response = MagicMock()
        auth_response.json.return_value = {
            "success": True,
            "result": {
                "track_id": "xyz789",
                "app_token": "test_token",
            },
        }

        # Mock the polling response
        poll_response = MagicMock()
        poll_response.json.return_value = {
            "success": True,
            "result": {
                "status": "granted",
                "challenge": "xxx",
                "password_salt": "yyy"  # NOSONAR : Test data
            },
        }

        mock_client.post.return_value = auth_response
        mock_client.get.return_value = poll_response

        with patch("builtins.print"):
            with patch("time.sleep"):
                with patch.object(sys, 'argv', ['p.py']):
                    exit_code = main()

        assert exit_code == 0
        mock_client.post.assert_called_once()
        mock_client.get.assert_called_once()


def test_main_failure_no_track_id() -> None:
    """Test main function when authorization response lacks track_id."""
    with patch("routerless.scripts.get_freebox_app_token.httpx.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        # Mock authorization response without track_id
        auth_response = MagicMock()
        auth_response.json.return_value = {
            "success": True,
            "result": {
                "app_token": "test_token",
            },
        }

        mock_client.post.return_value = auth_response

        with patch("builtins.print"):
            with patch.object(sys, 'argv', ['p.py']):
                exit_code = main()

        assert exit_code == 1
