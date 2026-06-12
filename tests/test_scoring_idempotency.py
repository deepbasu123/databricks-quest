"""Idempotent base-points award (PR03).

The scoring ledger is idempotent on ``idempotency_key`` (a UNIQUE column). These
tests fake ``db.execute`` to mimic Postgres' ``ON CONFLICT (idempotency_key) DO
NOTHING`` (first insert affects 1 row, a duplicate affects 0) and assert that:

- a passing task awards points once;
- repeating the same (event, team, task) does not double-award;
- a different team on the same task is its own award;
- federation children dedupe per workspace, not per team.
"""

import re
from contextlib import contextmanager

import pytest

from services.scoring_service import ScoringService, base_points_idempotency_key


class _FakeLedger:
    """Mimics UNIQUE(idempotency_key) ON CONFLICT DO NOTHING + a transaction.

    Models both write paths against one store: the autocommit ``db.execute``
    path (unaudited callers) and the ``db.transaction()`` cursor path (audited
    awards, P1-15). Audited awards write the scoring row **and** an
    ``event_audit_log`` row through the same cursor; both land in this fake so a
    test can assert the audit row is present exactly when a ledger row is.
    """

    def __init__(self):
        self.keys = set()
        self.rows = []
        self.audit_rows = []
        self.rowcount = 0

    def _apply(self, sql, params):
        up = " ".join(sql.split()).upper()
        if up.startswith("INSERT INTO SCORING_EVENTS"):
            key = params[-1]  # idempotency_key is the last positional param.
            if key in self.keys:
                self.rowcount = 0  # ON CONFLICT DO NOTHING
                return 0
            self.keys.add(key)
            self.rows.append(params)
            self.rowcount = 1
            return 1
        if up.startswith("INSERT INTO EVENT_AUDIT_LOG"):
            self.audit_rows.append(params)
            self.rowcount = 1
            return 1
        raise AssertionError(f"unexpected SQL: {up[:60]}")

    # Autocommit path: db.execute(sql, params) -> rowcount.
    def execute(self, sql, params=()):
        return self._apply(sql, params)

    # Transaction path: `with db.transaction() as cur: cur.execute(...)`.
    @contextmanager
    def transaction(self):
        ledger = self

        class _Cur:
            def execute(self, sql, params=()):
                ledger._apply(sql, params)

            @property
            def rowcount(self):
                return ledger.rowcount

        yield _Cur()


@pytest.fixture()
def ledger(monkeypatch):
    fake = _FakeLedger()
    import db

    monkeypatch.setattr(db, "execute", fake.execute)
    monkeypatch.setattr(db, "transaction", fake.transaction)
    return fake


def _award(svc, *, team_id=None, workspace_id=None, task_id="tsk_1", points=200):
    return svc.award_task_base_points(
        event_id="evt_1",
        task_id=task_id,
        points=points,
        attempt_id="att_x",
        team_id=team_id,
        workspace_id=workspace_id,
        user_id="labuser+1@awsbricks.com",
    )


# ── key shape ────────────────────────────────────────────────────────────────


def test_key_is_team_scoped_in_standalone():
    key = base_points_idempotency_key("evt_1", "tsk_1", team_id="team_red")
    assert key == "team:team_red:evt_1:tsk_1:base"


def test_key_is_workspace_scoped_for_federation():
    key = base_points_idempotency_key("evt_1", "tsk_1", workspace_id="ws-01")
    assert key == "ws-01:evt_1:tsk_1:base"


def test_team_and_workspace_keyspaces_never_collide():
    team_key = base_points_idempotency_key("e", "t", team_id="x")
    ws_key = base_points_idempotency_key("e", "t", workspace_id="x")
    assert team_key != ws_key


# ── single-award behaviour ───────────────────────────────────────────────────


def test_passing_task_awards_once(ledger):
    svc = ScoringService()
    first = _award(svc, team_id="team_red")
    assert first["awarded"] is True
    assert first["points"] == 200
    assert len(ledger.rows) == 1
    # P1-15: the award wrote its audit row atomically (same transaction).
    assert len(ledger.audit_rows) == 1


def test_repeat_submission_does_not_double_award(ledger):
    svc = ScoringService()
    _award(svc, team_id="team_red")
    second = _award(svc, team_id="team_red")
    assert second["awarded"] is False
    assert second["points"] == 0
    assert len(ledger.rows) == 1  # still only one ledger row
    # An idempotent replay writes neither a ledger row nor an audit row.
    assert len(ledger.audit_rows) == 1


def test_distinct_teams_each_get_their_own_award(ledger):
    svc = ScoringService()
    _award(svc, team_id="team_red")
    _award(svc, team_id="team_blue")
    assert len(ledger.rows) == 2


def test_federation_dedupes_per_workspace(ledger):
    svc = ScoringService()
    a = _award(svc, workspace_id="ws-01")
    b = _award(svc, workspace_id="ws-01")  # same attendee retries
    c = _award(svc, workspace_id="ws-02")  # different attendee
    assert a["awarded"] is True
    assert b["awarded"] is False
    assert c["awarded"] is True
    assert len(ledger.rows) == 2


def test_zero_point_task_writes_nothing(ledger):
    svc = ScoringService()
    out = _award(svc, team_id="team_red", points=0)
    assert out["awarded"] is False
    assert len(ledger.rows) == 0


def test_no_scope_writes_no_orphan_row(ledger):
    svc = ScoringService()
    out = _award(svc, team_id=None, workspace_id=None)
    assert out["awarded"] is False
    assert len(ledger.rows) == 0
