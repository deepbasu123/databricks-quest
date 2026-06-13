"""resolve_event_id: master auto-resolves its latest event without a pinned slug."""
import services.federation as fed
from services import federation


def test_master_falls_back_to_latest_event(monkeypatch):
    monkeypatch.setattr(fed.config, "QUEST_EVENT_SLUG", "")
    monkeypatch.setattr(fed.config, "is_child", lambda: False)
    monkeypatch.setattr(fed._events, "latest_event_id", lambda: "evt_latest")
    assert fed.resolve_event_id() == "evt_latest"


def test_child_without_slug_resolves_none(monkeypatch):
    monkeypatch.setattr(fed.config, "QUEST_EVENT_SLUG", "")
    monkeypatch.setattr(fed.config, "is_child", lambda: True)
    monkeypatch.setattr(fed._events, "latest_event_id", lambda: "evt_latest")
    assert fed.resolve_event_id() is None


def test_pinned_slug_wins(monkeypatch):
    monkeypatch.setattr(fed.config, "QUEST_EVENT_SLUG", "my-slug")
    monkeypatch.setattr(fed.config, "is_child", lambda: False)
    monkeypatch.setattr(fed._events, "get_event_by_slug", lambda s: {"event_id": "evt_pinned"})
    monkeypatch.setattr(fed._events, "latest_event_id", lambda: "evt_latest")
    assert fed.resolve_event_id() == "evt_pinned"
