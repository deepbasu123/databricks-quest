"""P2-4: TTL cache utility + adoption-leaderboard caching."""

import pytest
from fastapi.testclient import TestClient

from cache import TTLCache


# ── TTLCache utility (controllable clock) ─────────────────────────────────────


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1000.0}
    import cache

    monkeypatch.setattr(cache.time, "monotonic", lambda: now["t"])
    return now


def test_hit_then_expiry(clock):
    c = TTLCache(10)
    c.set("k", "v")
    assert c.get("k") == "v"
    clock["t"] += 9.9
    assert c.get("k") == "v"          # still fresh
    clock["t"] += 0.2
    assert c.get("k") is None          # expired (>= ttl)


def test_ttl_zero_disables(clock):
    c = TTLCache(0)
    c.set("k", "v")
    assert c.get("k") is None


def test_get_or_compute_caches_and_reuses(clock):
    c = TTLCache(10)
    calls = {"n": 0}

    def producer():
        calls["n"] += 1
        return "computed"

    assert c.get_or_compute("k", producer) == "computed"
    assert c.get_or_compute("k", producer) == "computed"
    assert calls["n"] == 1              # second call served from cache


def test_invalidate_and_clear(clock):
    c = TTLCache(10)
    c.set("a", 1)
    c.set("b", 2)
    c.invalidate("a")
    assert c.get("a") is None and c.get("b") == 2
    c.clear()
    assert c.get("b") is None


# ── /api/leaderboard caching ──────────────────────────────────────────────────


@pytest.fixture
def main_module():
    import main

    return main


@pytest.fixture
def client(main_module, monkeypatch):
    m = main_module
    m._leaderboard_cache.clear()
    return TestClient(m.app, raise_server_exceptions=False)


def test_leaderboard_polls_collapse_within_ttl(client, main_module, monkeypatch):
    m = main_module
    monkeypatch.setenv("LEADERBOARD_CACHE_TTL_SECONDS", "60")
    calls = {"n": 0}

    def fake_query(sql, params=()):
        calls["n"] += 1
        return [{"user_id": "u1", "total_points": 100}]

    monkeypatch.setattr(m, "execute_query", fake_query)

    a = client.get("/api/leaderboard?period=all").json()
    b = client.get("/api/leaderboard?period=all").json()
    assert a == b
    assert calls["n"] == 1              # second poll served from cache


def test_leaderboard_periods_cached_independently(client, main_module, monkeypatch):
    m = main_module
    monkeypatch.setenv("LEADERBOARD_CACHE_TTL_SECONDS", "60")
    calls = {"n": 0}
    monkeypatch.setattr(m, "execute_query",
                        lambda sql, params=(): calls.__setitem__("n", calls["n"] + 1) or [])

    client.get("/api/leaderboard?period=all")
    client.get("/api/leaderboard?period=weekly")
    assert calls["n"] == 2              # distinct keys → distinct queries
    client.get("/api/leaderboard?period=all")
    assert calls["n"] == 2              # both now cached


def test_ttl_zero_disables_leaderboard_cache(client, main_module, monkeypatch):
    m = main_module
    monkeypatch.setenv("LEADERBOARD_CACHE_TTL_SECONDS", "0")
    calls = {"n": 0}
    monkeypatch.setattr(m, "execute_query",
                        lambda sql, params=(): calls.__setitem__("n", calls["n"] + 1) or [])

    client.get("/api/leaderboard")
    client.get("/api/leaderboard")
    assert calls["n"] == 2              # no caching


def test_leaderboard_error_is_not_cached(client, main_module, monkeypatch):
    m = main_module
    monkeypatch.setenv("LEADERBOARD_CACHE_TTL_SECONDS", "60")
    state = {"fail": True, "calls": 0}

    def flaky(sql, params=()):
        state["calls"] += 1
        if state["fail"]:
            raise RuntimeError("db down")
        return [{"user_id": "u1"}]

    monkeypatch.setattr(m, "execute_query", flaky)

    assert client.get("/api/leaderboard").json()["leaderboard"] == []
    # Failure wasn't cached: the next poll retries and now succeeds.
    state["fail"] = False
    assert client.get("/api/leaderboard").json()["leaderboard"] == [{"user_id": "u1"}]
    assert state["calls"] == 2
