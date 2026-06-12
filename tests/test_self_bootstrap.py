"""Self-bootstrap helpers: in-process migration apply + event-writer role SQL.

These back the master/standalone startup hook that lets the app bootstrap its
own GameDay schema on boot (no external deploy step). DB-free: a fake connection
records the SQL so we can assert the contract without a live Postgres.
"""

from __future__ import annotations

import migrations.run_migrations as rm


class _FakeCursor:
    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._sink.append((sql, params))

    def fetchall(self):
        return []


class _FakeConn:
    def __init__(self):
        self.autocommit = False
        self.statements: list = []
        self.commits = 0

    def cursor(self):
        return _FakeCursor(self.statements)

    def commit(self):
        self.commits += 1


def test_provision_event_writer_role_builds_safe_sql():
    conn = _FakeConn()
    rm.provision_event_writer_role(conn, "quest_event_writer", "tok'en")
    assert len(conn.statements) == 1
    sql, params = conn.statements[0]
    # Executed with NO psycopg2 params, so the DO-block %I/%L reach the server.
    assert params is None
    assert "quest_event_writer" in sql
    assert "tok''en" in sql          # single quote escaped
    assert "CREATE ROLE" in sql and "GRANT INSERT" in sql


def test_apply_with_connection_noop_when_no_migrations(monkeypatch):
    monkeypatch.setattr(rm, "discover_migrations", lambda d: [])
    # No connection use at all when there's nothing to apply.
    assert rm.apply_with_connection(object()) == 0


def test_apply_with_connection_applies_only_unapplied(monkeypatch):
    migs = [("001_a", "a", "/x/001_a.sql"), ("002_b", "b", "/x/002_b.sql")]
    monkeypatch.setattr(rm, "discover_migrations", lambda d: migs)
    applied_calls: list = []

    class _Backend:
        def __init__(self, conn):
            pass

        def ensure_table(self):
            pass

        def applied_versions(self):
            return {"001_a"}  # first already applied

        def apply(self, version, description, path):
            applied_calls.append(version)

    monkeypatch.setattr(rm, "Psycopg2Backend", _Backend)
    newly = rm.apply_with_connection(object())
    assert newly == 1
    assert applied_calls == ["002_b"]
