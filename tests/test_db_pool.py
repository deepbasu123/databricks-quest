"""P1-10: token-aware bounded connection pool semantics.

Exercises the pool with fake connections (no real DB) — borrow/return reuse,
validate-on-borrow replacement of dead connections, fresh-credential factory on
growth, and the concurrency bound. The live behaviour (concurrency + non-blocking
async offload) is validated separately against a real Lakebase.
"""

import threading
import time

import pytest

import db


class _FakeConn:
    def __init__(self, alive=True):
        self.alive = alive
        self.closed = False

    def cursor(self):
        conn = self

        class _Cur:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def execute(self_, sql, params=None):
                if not conn.alive:
                    raise RuntimeError("dead connection")

        return _Cur()

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def test_borrow_returns_and_reuses():
    made = []
    pool = db._TokenAwarePool(maxconn=2, factory=lambda: made.append(_FakeConn()) or made[-1])
    c1 = pool.getconn()
    pool.putconn(c1)
    c2 = pool.getconn()
    assert c1 is c2          # idle connection reused, not re-created
    assert len(made) == 1


def test_dead_connection_is_replaced_on_borrow():
    made = []

    def factory():
        c = _FakeConn()
        made.append(c)
        return c

    pool = db._TokenAwarePool(maxconn=2, factory=factory)
    c1 = pool.getconn()
    c1.alive = False         # token expired / connection died while idle
    pool.putconn(c1)
    c2 = pool.getconn()      # validate-on-borrow rejects the dead one
    assert c2 is not c1
    assert c1.closed is True
    assert len(made) == 2    # factory minted a fresh connection (fresh credential)


def test_broken_connection_closed_not_returned():
    pool = db._TokenAwarePool(maxconn=2, factory=_FakeConn)
    c = pool.getconn()
    pool.putconn(c, close=True)
    assert c.closed is True


def test_bound_blocks_until_release():
    pool = db._TokenAwarePool(maxconn=1, factory=_FakeConn)
    held = pool.getconn()
    released = threading.Event()
    got_second = threading.Event()

    def borrow_second():
        c = pool.getconn()      # must block until `held` is returned
        got_second.set()
        pool.putconn(c)

    t = threading.Thread(target=borrow_second)
    t.start()
    assert not got_second.wait(0.3)   # blocked: pool is at its max of 1
    pool.putconn(held)
    assert got_second.wait(1.0)       # unblocked once the slot frees
    t.join()


def test_lease_uses_pool_and_returns_connection(monkeypatch):
    # _lease leases from the pool, yields the conn, and returns it afterward.
    pool = db._TokenAwarePool(maxconn=1, factory=_FakeConn)
    monkeypatch.setattr(db, "_get_pool", lambda: pool)
    with db._lease(autocommit=True) as conn:
        assert isinstance(conn, _FakeConn)
    # Returned (not leaked): a second lease succeeds immediately.
    with db._lease(autocommit=True) as conn2:
        assert isinstance(conn2, _FakeConn)


class _TxFakeConn:
    """Fake mimicking psycopg2: a query on a non-autocommit conn opens a tx, and
    setting autocommit while in a tx raises (set_session error)."""
    def __init__(self):
        self.autocommit = False
        self.in_tx = False
        self.closed = False
        self.rollbacks = 0

    def cursor(self):
        conn = self

        class _Cur:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
            def execute(self_, sql, params=None):
                if not conn.autocommit:
                    conn.in_tx = True  # implicit BEGIN
        return _Cur()

    def rollback(self):
        self.in_tx = False
        self.rollbacks += 1

    def close(self):
        self.closed = True


def test_validate_probe_does_not_leave_open_transaction():
    """Regression: the validate-on-borrow probe must not leave a connection in a
    transaction, else the next lease's autocommit switch fails."""
    conn = _TxFakeConn()
    assert db._TokenAwarePool._alive(conn) is True
    assert conn.in_tx is False          # probe rolled back its implicit tx
    assert conn.rollbacks >= 1
