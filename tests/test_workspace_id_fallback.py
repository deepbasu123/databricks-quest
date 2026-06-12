"""QUEST_WORKSPACE_ID falls back to the Apps-injected DATABRICKS_WORKSPACE_ID."""
import importlib
import os


def _reload_config(monkeypatch, env: dict):
    for k in ("QUEST_WORKSPACE_ID", "DATABRICKS_WORKSPACE_ID"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import config
    return importlib.reload(config)


def test_explicit_wins(monkeypatch):
    c = _reload_config(monkeypatch, {"QUEST_WORKSPACE_ID": "111", "DATABRICKS_WORKSPACE_ID": "222"})
    assert c.QUEST_WORKSPACE_ID == "111"


def test_falls_back_to_databricks_workspace_id(monkeypatch):
    c = _reload_config(monkeypatch, {"DATABRICKS_WORKSPACE_ID": "222"})
    assert c.QUEST_WORKSPACE_ID == "222"


def test_empty_when_neither(monkeypatch):
    c = _reload_config(monkeypatch, {})
    assert c.QUEST_WORKSPACE_ID == ""
