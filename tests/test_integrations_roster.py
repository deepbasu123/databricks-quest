"""C2a: service-authed roster upsert endpoint + service-token auth."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def main_module():
    import main

    return main


@pytest.fixture
def client(main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m.config, "event_mode_enabled", lambda: True)
    monkeypatch.setattr(m.config, "is_child", lambda: False)
    monkeypatch.setattr(m, "_resolve_event_or_404", lambda e: "evt_1")
    return TestClient(m.app, raise_server_exceptions=False)


_BODY = {
    "attendees": [
        {"workspace_id": "100", "lab_user_email": "a@x.com", "team_name": "Reds",
         "display_name": "A", "real_email": "a.real@x.com"},
        {"workspace_id": "101", "lab_user_email": "b@x.com", "team_name": "Blues"},
    ]
}


def test_upsert_requires_service_token_503_when_unconfigured(client, main_module, monkeypatch):
    monkeypatch.setattr(main_module, "QUEST_SERVICE_TOKEN", "")
    res = client.put("/api/integrations/roster/evt_1", json=_BODY)
    assert res.status_code == 503


def test_upsert_rejects_missing_or_bad_token(client, main_module, monkeypatch):
    monkeypatch.setattr(main_module, "QUEST_SERVICE_TOKEN", "s3cret")
    assert client.put("/api/integrations/roster/evt_1", json=_BODY).status_code == 401
    bad = client.put("/api/integrations/roster/evt_1", json=_BODY,
                     headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401


def test_upsert_converts_to_csv_and_calls_importer(client, main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m, "QUEST_SERVICE_TOKEN", "s3cret")
    captured = {}

    def fake_import(event_id, csv_text):
        captured["event_id"] = event_id
        captured["csv"] = csv_text
        return {"rows": 2, "teams_created": 2, "participants_created": 2, "identities_mapped": 2}

    monkeypatch.setattr(m.federation_repo, "import_roster", fake_import)
    monkeypatch.setattr(m, "record_audit", lambda **k: None, raising=False)

    res = client.put("/api/integrations/roster/evt_1", json=_BODY,
                     headers={"Authorization": "Bearer s3cret"})
    assert res.status_code == 200, res.text
    assert res.json()["identities_mapped"] == 2
    assert captured["event_id"] == "evt_1"
    csv_text = captured["csv"]
    # Canonical header + both attendees present.
    assert csv_text.splitlines()[0] == "workspace_id,lab_user_email,team_name,display_name,real_email"
    assert "a@x.com" in csv_text and "Reds" in csv_text
    assert "b@x.com" in csv_text and "Blues" in csv_text


def test_upsert_404_when_event_mode_off(client, main_module, monkeypatch):
    monkeypatch.setattr(main_module, "QUEST_SERVICE_TOKEN", "s3cret")
    monkeypatch.setattr(main_module.config, "event_mode_enabled", lambda: False)
    res = client.put("/api/integrations/roster/evt_1", json=_BODY,
                     headers={"Authorization": "Bearer s3cret"})
    assert res.status_code == 404
