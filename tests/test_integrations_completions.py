"""C4b: service-authed completions ingestion (Control Tower attestation)."""

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
    monkeypatch.setattr(m, "QUEST_SERVICE_TOKEN", "s3cret")
    monkeypatch.setattr(m, "record_audit", lambda **k: None, raising=False)
    # Default: active event so the lifecycle gate passes.
    monkeypatch.setattr(m.events_repo, "get_event", lambda e: {"status": "active"})
    return TestClient(m.app, raise_server_exceptions=False)


_AUTH = {"Authorization": "Bearer s3cret"}


def _body(**over):
    item = {"task_id": "t1", "lab_user_email": "a@x.com", "workspace_id": "100"}
    item.update(over)
    return {"completions": [item]}


def test_requires_service_token(client, main_module, monkeypatch):
    # No token header → 401.
    assert client.post("/api/integrations/events/evt_1/completions",
                       json=_body()).status_code == 401
    # Unconfigured server token → 503.
    monkeypatch.setattr(main_module, "QUEST_SERVICE_TOKEN", "")
    assert client.post("/api/integrations/events/evt_1/completions",
                       json=_body(), headers=_AUTH).status_code == 503


def test_closed_event_rejects_batch_409(client, main_module, monkeypatch):
    monkeypatch.setattr(main_module.events_repo, "get_event", lambda e: {"status": "completed"})
    res = client.post("/api/integrations/events/evt_1/completions",
                      json=_body(), headers=_AUTH)
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "EVENT_NOT_ACTIVE"


def test_attested_award_uses_idempotent_scoring(client, main_module, monkeypatch):
    m = main_module
    captured = {}

    monkeypatch.setattr(m.quest_packs_repo, "get_task",
                        lambda tid: {"points": 50, "quest_id": "q1", "accepts_attested": True})
    monkeypatch.setattr(m.federation_repo, "resolve_identity",
                        lambda e, w, u: {"team_id": "team_9"})

    def fake_award(**kw):
        captured.update(kw)
        return {"awarded": True, "points": 50, "scoring_event_id": "se_1"}

    monkeypatch.setattr(m.default_scoring_service, "award_task_base_points", fake_award)

    res = client.post("/api/integrations/events/evt_1/completions",
                      json=_body(), headers=_AUTH)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["awarded"] == 1 and out["processed"] == 1
    row = out["results"][0]
    assert row["status"] == "awarded" and row["points"] == 50
    assert row["scoring_event_id"] == "se_1"
    # Scored through the shared idempotent path, attributed to the resolved team,
    # and stamped as Control-Tower-sourced.
    assert captured["event_id"] == "evt_1"
    assert captured["task_id"] == "t1" and captured["points"] == 50
    assert captured["team_id"] == "team_9"
    assert captured["workspace_id"] == "100"
    assert captured["created_by"] == "service:control-tower"


def test_attested_idempotent_replay_reports_already_awarded(client, main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m.quest_packs_repo, "get_task",
                        lambda tid: {"points": 50, "quest_id": "q1", "accepts_attested": True})
    monkeypatch.setattr(m.federation_repo, "resolve_identity", lambda e, w, u: {"team_id": "team_9"})
    # Repository says the award already happened (idempotent no-op).
    monkeypatch.setattr(m.default_scoring_service, "award_task_base_points",
                        lambda **kw: {"awarded": False, "points": 0, "scoring_event_id": None})

    res = client.post("/api/integrations/events/evt_1/completions",
                      json=_body(), headers=_AUTH)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["awarded"] == 0
    row = out["results"][0]
    assert row["status"] == "skipped" and row["already_awarded"] is True


def test_attestation_refused_unless_task_opts_in(client, main_module, monkeypatch):
    m = main_module
    # Task does not set accepts_attested → integrity control refuses the award.
    monkeypatch.setattr(m.quest_packs_repo, "get_task",
                        lambda tid: {"points": 50, "quest_id": "q1"})
    called = {"award": False}
    monkeypatch.setattr(m.default_scoring_service, "award_task_base_points",
                        lambda **kw: called.__setitem__("award", True))

    res = client.post("/api/integrations/events/evt_1/completions",
                      json=_body(), headers=_AUTH)
    assert res.status_code == 200, res.text
    assert res.json()["results"][0]["status"] == "attestation_not_allowed"
    assert called["award"] is False


def test_unknown_task_is_skipped_not_fatal(client, main_module, monkeypatch):
    m = main_module
    monkeypatch.setattr(m.quest_packs_repo, "get_task", lambda tid: None)
    res = client.post("/api/integrations/events/evt_1/completions",
                      json=_body(), headers=_AUTH)
    assert res.status_code == 200, res.text
    assert res.json()["results"][0]["status"] == "task_not_found"


def test_trigger_mode_records_revalidation_request(client, main_module, monkeypatch):
    m = main_module
    called = {"get_task": False}
    monkeypatch.setattr(m.quest_packs_repo, "get_task",
                        lambda tid: called.__setitem__("get_task", True))
    res = client.post("/api/integrations/events/evt_1/completions",
                      json=_body(mode="trigger"), headers=_AUTH)
    assert res.status_code == 200, res.text
    assert res.json()["results"][0]["status"] == "revalidation_requested"
    # Trigger mode does not award directly; it does not even resolve the task here.
    assert called["get_task"] is False
