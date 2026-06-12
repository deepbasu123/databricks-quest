"""P1-15: audit rows commit in the same transaction as the scoring mutation.

`record_audit(cursor=...)` writes through the caller's open transaction and
propagates failures (so the whole transaction rolls back), instead of the
best-effort autocommit write that a crash could drop after the score committed.
"""

from contextlib import contextmanager

import pytest

import db
from services.audit import record_audit
from services.scoring_service import ScoringService


class _Cur:
    """Records executes; can be told to fail on a given table."""

    def __init__(self, fail_on=None):
        self.calls = []
        self._fail_on = fail_on

    def execute(self, sql, params=()):
        up = " ".join(sql.split()).upper()
        self.calls.append(up)
        if self._fail_on and self._fail_on.upper() in up:
            raise RuntimeError("simulated write failure")


# ── record_audit primitive ───────────────────────────────────────────────────


def test_record_audit_with_cursor_writes_through_it():
    cur = _Cur()
    audit_id = record_audit("event.start", actor_user_id="host@x.com",
                            event_id="evt_1", cursor=cur)
    assert audit_id and audit_id.startswith("aud_")
    assert len(cur.calls) == 1 and "EVENT_AUDIT_LOG" in cur.calls[0]


def test_record_audit_with_cursor_propagates_failure():
    # Transactional mode must NOT swallow — the caller's transaction rolls back.
    cur = _Cur(fail_on="event_audit_log")
    with pytest.raises(RuntimeError):
        record_audit("event.start", cursor=cur)


def test_record_audit_best_effort_swallows_failure(monkeypatch):
    def boom(sql, params=()):
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "execute", boom)
    # No cursor → best-effort: returns None instead of raising.
    assert record_audit("event.start") is None


# ── atomicity of the audited award ───────────────────────────────────────────


class _TxnLedger:
    """A fake whose audit insert fails, to prove the award rolls back with it."""

    def __init__(self, fail_audit=False):
        self.scoring_rows = []
        self.audit_rows = []
        self.committed = None
        self._fail_audit = fail_audit
        self.rowcount = 0

    @contextmanager
    def transaction(self):
        # Stage writes; only "commit" them to the visible lists if the whole
        # block succeeds — modelling Postgres rollback-on-exception.
        staged_scoring, staged_audit = [], []
        ledger = self

        class _C:
            def execute(self, sql, params=()):
                up = " ".join(sql.split()).upper()
                if up.startswith("INSERT INTO SCORING_EVENTS"):
                    staged_scoring.append(params)
                    ledger.rowcount = 1
                elif up.startswith("INSERT INTO EVENT_AUDIT_LOG"):
                    if ledger._fail_audit:
                        raise RuntimeError("audit insert failed")
                    staged_audit.append(params)

            @property
            def rowcount(self):
                return ledger.rowcount

        try:
            yield _C()
        except Exception:
            self.committed = False
            raise
        self.scoring_rows.extend(staged_scoring)
        self.audit_rows.extend(staged_audit)
        self.committed = True


def test_award_and_audit_commit_together(monkeypatch):
    fake = _TxnLedger()
    monkeypatch.setattr(db, "transaction", fake.transaction)

    out = ScoringService().award_task_base_points(
        event_id="evt_1", task_id="t1", points=100, attempt_id="att_1",
        team_id="team_red", user_id="u@x.com",
    )
    assert out["awarded"] is True
    assert fake.committed is True
    assert len(fake.scoring_rows) == 1 and len(fake.audit_rows) == 1


def test_audit_failure_rolls_back_the_award(monkeypatch):
    fake = _TxnLedger(fail_audit=True)
    monkeypatch.setattr(db, "transaction", fake.transaction)

    with pytest.raises(RuntimeError):
        ScoringService().award_task_base_points(
            event_id="evt_1", task_id="t1", points=100, attempt_id="att_1",
            team_id="team_red", user_id="u@x.com",
        )
    # The whole transaction rolled back: neither row is visible.
    assert fake.committed is False
    assert fake.scoring_rows == [] and fake.audit_rows == []


def test_manual_adjustment_audit_joins_the_transaction(monkeypatch):
    from repositories.scoring import ScoringRepository

    stmts = []

    class _Cur:
        def execute(self, sql, params=()):
            stmts.append(" ".join(sql.split()).upper())

    @contextmanager
    def fake_tx():
        yield _Cur()

    monkeypatch.setattr(db, "transaction", fake_tx)
    ScoringRepository().record_manual_adjustment(
        event_id="evt_1", points_delta=50, reason="bonus", created_by="h@x",
        team_id="team_red",
        audit={"action": "score.adjust", "actor_user_id": "h@x",
               "event_id": "evt_1", "target_type": "team", "target_id": "team_red",
               "payload": {"points_delta": 50}},
    )
    # All three writes — adjustment, ledger, audit — in the one transaction.
    assert len(stmts) == 3
    assert "MANUAL_ADJUSTMENTS" in stmts[0]
    assert "SCORING_EVENTS" in stmts[1]
    assert "EVENT_AUDIT_LOG" in stmts[2]
