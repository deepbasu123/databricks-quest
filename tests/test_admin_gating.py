"""Admin page gating — /api/admin/* is restricted by ``QUEST_ADMIN_ALLOWLIST``.

The allowlist is set by ``deploy.sh --admins`` (defaulting to the deploying
user). When set, non-allowlisted users get 403; when unset the endpoints stay
open (local-dev parity). ``/api/profile`` exposes ``is_admin`` for nav gating.
"""

import pytest
from fastapi import HTTPException


@pytest.fixture
def main_module():
    import main

    return main


def test_open_when_allowlist_unset(main_module, monkeypatch):
    monkeypatch.setattr(main_module, "QUEST_ADMIN_ALLOWLIST", [])
    assert main_module.is_admin_user("anyone@corp.com") is True
    assert main_module.is_admin_user("") is True


def test_allowlist_restricts_to_members(main_module, monkeypatch):
    monkeypatch.setattr(
        main_module, "QUEST_ADMIN_ALLOWLIST", ["alice@corp.com", "bob@corp.com"]
    )
    assert main_module.is_admin_user("alice@corp.com") is True
    # case-insensitive match
    assert main_module.is_admin_user("BOB@corp.com") is True
    assert main_module.is_admin_user("mallory@corp.com") is False
    assert main_module.is_admin_user("") is False


def test_require_admin_raises_403_for_non_admin(main_module, monkeypatch):
    monkeypatch.setattr(main_module, "QUEST_ADMIN_ALLOWLIST", ["alice@corp.com"])
    monkeypatch.setattr(main_module, "get_user_email", lambda req: "mallory@corp.com")
    with pytest.raises(HTTPException) as exc:
        main_module.require_admin(request=None)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == "FORBIDDEN"


def test_require_admin_allows_member(main_module, monkeypatch):
    monkeypatch.setattr(main_module, "QUEST_ADMIN_ALLOWLIST", ["alice@corp.com"])
    monkeypatch.setattr(main_module, "get_user_email", lambda req: "alice@corp.com")
    assert main_module.require_admin(request=None) == "alice@corp.com"
