"""require_host accepts the Control Tower service token as host authority (C2).

So CT can drive the event/pack/announcement lifecycle with the same shared
bearer it uses for roster/completions, without a human workspace identity.
"""

from __future__ import annotations

import types

import pytest
from fastapi import HTTPException

import main


def _req(auth: str | None = None, path_params: dict | None = None):
    headers = {}
    if auth is not None:
        headers["Authorization"] = auth
    return types.SimpleNamespace(
        headers=headers, path_params=path_params or {}
    )


@pytest.fixture(autouse=True)
def _event_mode_on(monkeypatch):
    monkeypatch.setattr(main.config, "event_mode_enabled", lambda: True)


def test_service_token_confers_host(monkeypatch):
    monkeypatch.setattr(main, "QUEST_SERVICE_TOKEN", "s3cret")
    actor = main.require_host(_req(auth="Bearer s3cret"))
    assert actor == "service:control-tower"


def test_wrong_token_falls_back_to_host_user_check(monkeypatch):
    monkeypatch.setattr(main, "QUEST_SERVICE_TOKEN", "s3cret")
    # Not the service token, and not a host user → 403.
    monkeypatch.setattr(main, "get_user_email", lambda r: "nobody@x.com")
    monkeypatch.setattr(main, "is_host_user", lambda u, e: False)
    with pytest.raises(HTTPException) as ei:
        main.require_host(_req(auth="Bearer wrong"))
    assert ei.value.status_code == 403


def test_no_token_configured_still_allows_host_user(monkeypatch):
    monkeypatch.setattr(main, "QUEST_SERVICE_TOKEN", "")
    monkeypatch.setattr(main, "get_user_email", lambda r: "host@x.com")
    monkeypatch.setattr(main, "is_host_user", lambda u, e: True)
    assert main.require_host(_req()) == "host@x.com"


def test_service_token_via_custom_header(monkeypatch):
    """Behind the Apps proxy the token rides X-Quest-Service-Token (Authorization
    is consumed by the proxy for OAuth)."""
    monkeypatch.setattr(main, "QUEST_SERVICE_TOKEN", "s3cret")
    req = types.SimpleNamespace(
        headers={"Authorization": "Bearer some-databricks-oauth", "X-Quest-Service-Token": "s3cret"},
        path_params={},
    )
    assert main.require_host(req) == "service:control-tower"
    assert main.require_service_token(req) == "service:control-tower"


def test_custom_header_wrong_token_rejected(monkeypatch):
    monkeypatch.setattr(main, "QUEST_SERVICE_TOKEN", "s3cret")
    req = types.SimpleNamespace(headers={"X-Quest-Service-Token": "nope"}, path_params={})
    with pytest.raises(HTTPException) as ei:
        main.require_service_token(req)
    assert ei.value.status_code == 401
