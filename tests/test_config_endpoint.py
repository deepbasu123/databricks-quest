"""P2-11: GET /api/config — single source of truth for gamification config."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def main_module():
    import main

    return main


@pytest.fixture
def client(main_module):
    return TestClient(main_module.app, raise_server_exceptions=False)


def test_config_serves_levels_in_ladder_order(client, main_module):
    res = client.get("/api/config")
    assert res.status_code == 200
    body = res.json()
    assert body["schema_version"] == main_module.CONFIG_SCHEMA_VERSION

    # Levels mirror the backend source, ordered highest→lowest (the order the
    # leaderboard renders) so the frontend never hardcodes its own copy.
    names = [lvl["name"] for lvl in body["levels"]]
    assert names == [name for name, _ in main_module.LEVEL_THRESHOLDS]
    thresholds = [lvl["threshold"] for lvl in body["levels"]]
    assert thresholds == sorted(thresholds, reverse=True)
    assert body["levels"][0] == {"name": "Elite", "threshold": 5000}
    assert body["levels"][-1] == {"name": "Bronze", "threshold": 0}


def test_config_includes_missions_badges_and_ratio(client, main_module):
    body = client.get("/api/config").json()
    assert body["consumption_points_ratio"] == main_module.CONSUMPTION_POINTS_RATIO
    assert len(body["missions"]) == len(main_module.MISSION_DEFINITIONS)
    assert len(body["badges"]) == len(main_module.BADGE_DEFINITIONS)
    # Each mission/badge carries the fields the client renders.
    assert all({"id", "name", "description"} <= set(m) for m in body["missions"])
    assert all("id" in b and "name" in b for b in body["badges"])


def test_config_is_a_copy_not_the_backend_lists(main_module):
    # build_quest_config must hand out copies so a caller can't mutate the
    # module-level source of truth.
    cfg = main_module.build_quest_config()
    cfg["missions"][0]["name"] = "MUTATED"
    assert main_module.MISSION_DEFINITIONS[0]["name"] != "MUTATED"


def test_config_needs_no_auth_or_db(client, monkeypatch, main_module):
    # Reference data, not user state: it must not touch the DB (so it works even
    # when Lakebase is down) and needs no forwarded identity.
    def boom(*a, **k):
        raise AssertionError("config endpoint must not query the DB")

    monkeypatch.setattr(main_module, "execute_query", boom, raising=False)
    assert client.get("/api/config").status_code == 200
