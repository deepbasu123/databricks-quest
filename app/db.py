"""Centralized Lakebase (Postgres) connection handling for Databricks Quest.

This module is the single source of truth for connecting to Lakebase. Both the
FastAPI app and the migration runner use it so connection, auth, and
serialization behaviour stay consistent.

Connection strategy:
- In the running app, credentials come from the Databricks workspace identity
  (service principal OAuth token) via the Databricks SDK. Tokens are short-lived
  so the connection is cached for ~45 minutes and revalidated on reuse.
- Callers (e.g. the migration runner during deploy) may pass explicit
  ``user``/``password`` to bypass the SDK and connect with a pre-minted
  Lakebase credential.

The app must degrade gracefully when Lakebase is unavailable — callers are
expected to wrap query helpers in try/except and fall back to empty/default
responses, exactly as the adoption-mode endpoints already do.
"""

import os
import time
import logging
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("databricks-quest.db")

LAKEBASE_HOST = os.getenv("LAKEBASE_HOST", "")
LAKEBASE_DB = os.getenv("LAKEBASE_DB", "quest_db")
LAKEBASE_PORT = int(os.getenv("LAKEBASE_PORT", "5432"))

# OAuth tokens are valid ~1h; refresh the cached connection well before expiry.
_CONN_TTL_SECONDS = 2700  # 45 minutes
_conn_cache: Dict[str, Any] = {"conn": None, "expiry": 0}


def get_workspace_client():
    """Return a Databricks SDK WorkspaceClient.

    Imported lazily so this module can be imported in environments where the
    SDK is unavailable (e.g. a bare migration run with explicit credentials).
    """
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient()


def serialize(obj: Any) -> Any:
    """Convert Postgres/Python types into JSON-serializable values."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    return obj


def _workspace_credentials() -> Tuple[str, str]:
    """Resolve (user, password) from the Databricks workspace identity."""
    w = get_workspace_client()
    headers = w.config.authenticate()
    auth_header = headers.get("Authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else w.config.token
    user = w.current_user.me().user_name
    return user, token


def _connect(
    host: str,
    dbname: str,
    user: str,
    password: str,
    port: int,
    connect_timeout: int = 10,
):
    import psycopg2

    conn = psycopg2.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
        sslmode="require",
        connect_timeout=connect_timeout,
    )
    conn.autocommit = True
    return conn


def get_connection(
    host: Optional[str] = None,
    dbname: Optional[str] = None,
    port: Optional[int] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
    use_cache: bool = True,
):
    """Return a live psycopg2 connection to Lakebase.

    When ``user``/``password`` are supplied the connection is opened directly
    with those credentials and is NOT cached (used by the migration runner with
    a pre-minted credential). Otherwise credentials are resolved from the
    Databricks workspace identity and the connection is cached and revalidated.
    """
    host = host or LAKEBASE_HOST
    dbname = dbname or LAKEBASE_DB
    port = port or LAKEBASE_PORT

    if not host:
        raise RuntimeError("LAKEBASE_HOST not configured")

    # Explicit-credential path (no caching, no SDK).
    if user and password:
        return _connect(host, dbname, user, password, port)

    now = time.time()
    cached = _conn_cache["conn"]
    if use_cache and cached and _conn_cache["expiry"] > now:
        try:
            with cached.cursor() as c:
                c.execute("SELECT 1")
            return cached
        except Exception:
            try:
                cached.close()
            except Exception:
                pass

    resolved_user, token = _workspace_credentials()
    conn = _connect(host, dbname, resolved_user, token, port)
    if use_cache:
        _conn_cache["conn"] = conn
        _conn_cache["expiry"] = now + _CONN_TTL_SECONDS
    logger.info("Lakebase connection established as %s", resolved_user)
    return conn


# Backwards-compatible alias: adoption-mode code historically called this name.
def get_lakebase_connection():
    return get_connection()


def execute_query(query: str, params: Tuple = ()) -> List[Dict[str, Any]]:
    """Run a read query and return a list of JSON-serializable dict rows."""
    import psycopg2.extras

    conn = get_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params)
        if cur.description:
            rows = cur.fetchall()
            return [{k: serialize(v) for k, v in dict(row).items()} for row in rows]
        return []


def execute(query: str, params: Tuple = ()) -> int:
    """Run a write statement and return the affected row count.

    Uses the cached (autocommit) connection. Mutation callers that need
    multi-statement atomicity should manage their own transaction.
    """
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(query, params)
        return cur.rowcount


def healthcheck() -> bool:
    """Return True if a trivial query succeeds against Lakebase."""
    try:
        return len(execute_query("SELECT 1 AS ok")) > 0
    except Exception:
        return False


def applied_migrations() -> List[str]:
    """Return the list of applied migration versions, oldest first.

    Returns an empty list if Lakebase is unreachable or the
    ``schema_migrations`` table does not yet exist, so callers (e.g.
    ``/api/health``) never fail because migrations have not run.
    """
    try:
        rows = execute_query(
            "SELECT version FROM schema_migrations ORDER BY version ASC"
        )
        return [r["version"] for r in rows]
    except Exception:
        return []
