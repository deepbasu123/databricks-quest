"""Identity defense-in-depth (gap P1-4).

get_user_email must not mint a silent anonymous identity in Event Mode, and —
when a proxy shared secret is configured — must reject requests that did not
transit the trusted proxy.
"""

import pytest
from fastapi import HTTPException


@pytest.fixture
def main_module():
    import main

    return main


class _Req:
    def __init__(self, headers: dict[str, str]):
        self.headers = headers


def test_forwarded_email_is_returned(main_module, monkeypatch):
    monkeypatch.delenv("QUEST_PROXY_SHARED_SECRET", raising=False)
    req = _Req({"X-Forwarded-Email": "alice@x.com"})
    assert main_module.get_user_email(req) == "alice@x.com"


def test_event_mode_denies_anonymous(main_module, monkeypatch):
    monkeypatch.delenv("QUEST_PROXY_SHARED_SECRET", raising=False)
    monkeypatch.delenv("QUEST_DEFAULT_USER", raising=False)
    monkeypatch.setattr(main_module.config, "event_mode_enabled", lambda: True)
    with pytest.raises(HTTPException) as exc:
        main_module.get_user_email(_Req({}))
    assert exc.value.status_code == 401


def test_event_mode_allows_explicit_default_user(main_module, monkeypatch):
    monkeypatch.delenv("QUEST_PROXY_SHARED_SECRET", raising=False)
    monkeypatch.setenv("QUEST_DEFAULT_USER", "dev@x.com")
    monkeypatch.setattr(main_module.config, "event_mode_enabled", lambda: True)
    assert main_module.get_user_email(_Req({})) == "dev@x.com"


def test_adoption_mode_keeps_legacy_fallback(main_module, monkeypatch):
    monkeypatch.delenv("QUEST_PROXY_SHARED_SECRET", raising=False)
    monkeypatch.delenv("QUEST_DEFAULT_USER", raising=False)
    monkeypatch.setattr(main_module.config, "event_mode_enabled", lambda: False)
    assert main_module.get_user_email(_Req({})) == "unknown@example.com"


def test_proxy_secret_rejects_missing_header(main_module, monkeypatch):
    monkeypatch.setenv("QUEST_PROXY_SHARED_SECRET", "s3cret")
    with pytest.raises(HTTPException) as exc:
        main_module.get_user_email(_Req({"X-Forwarded-Email": "alice@x.com"}))
    assert exc.value.status_code == 401


def test_proxy_secret_rejects_wrong_header(main_module, monkeypatch):
    monkeypatch.setenv("QUEST_PROXY_SHARED_SECRET", "s3cret")
    with pytest.raises(HTTPException):
        main_module.get_user_email(
            _Req({"X-Forwarded-Email": "alice@x.com", "X-Quest-Proxy-Secret": "nope"})
        )


def test_proxy_secret_accepts_matching_header(main_module, monkeypatch):
    monkeypatch.setenv("QUEST_PROXY_SHARED_SECRET", "s3cret")
    email = main_module.get_user_email(
        _Req({"X-Forwarded-Email": "alice@x.com", "X-Quest-Proxy-Secret": "s3cret"})
    )
    assert email == "alice@x.com"
