"""C4b: get_task surfaces the attestation opt-in from task metadata.

Found in live M1 validation against a real Lakebase: the C4b completions
endpoint checks ``task['accepts_attested']``, but ``get_task`` never selected or
derived it, so the column was always absent and C4b would refuse every attested
completion. ``accepts_attested`` is carried in ``quest_tasks.metadata_json`` and
derived here.
"""

import pytest

import db
from repositories.quest_packs import QuestPacksRepository


def _stub_row(monkeypatch, row):
    monkeypatch.setattr(db, "execute_query", lambda sql, params=(): [row] if row else [])


def test_accepts_attested_true_from_dict_metadata(monkeypatch):
    _stub_row(monkeypatch, {"task_id": "t1", "points": 50,
                            "metadata_json": {"accepts_attested": True}})
    task = QuestPacksRepository().get_task("t1")
    assert task["accepts_attested"] is True


def test_accepts_attested_true_from_json_string_metadata(monkeypatch):
    _stub_row(monkeypatch, {"task_id": "t1", "metadata_json": '{"accepts_attested": true}'})
    assert QuestPacksRepository().get_task("t1")["accepts_attested"] is True


def test_accepts_attested_false_when_absent_or_null(monkeypatch):
    _stub_row(monkeypatch, {"task_id": "t1", "metadata_json": None})
    assert QuestPacksRepository().get_task("t1")["accepts_attested"] is False
    _stub_row(monkeypatch, {"task_id": "t1", "metadata_json": {"other": 1}})
    assert QuestPacksRepository().get_task("t1")["accepts_attested"] is False


def test_unknown_task_returns_none(monkeypatch):
    _stub_row(monkeypatch, None)
    assert QuestPacksRepository().get_task("nope") is None
