"""Audit logging for GameDay mutations.

Every host/admin/player mutation should call :func:`record_audit` so there is a
durable trail in ``event_audit_log``.

Two modes (P1-15):

- **Best-effort (default, ``cursor=None``):** writes in its own autocommit
  transaction; a failure is logged but never raised, so it cannot take down the
  operation it records. Use for actions where a missing audit row is tolerable.
- **Transactional (``cursor=<open cursor>``):** writes through the caller's open
  transaction and **does raise** on failure, so the audit row commits atomically
  with the mutation it describes (or both roll back). Use whenever the audit
  trail must be guaranteed — e.g. score mutations, where a committed points
  change with no audit row is an integrity hole.

Never store secrets, tokens, or raw credentials in ``payload_json`` — store a
short, safe summary only.
"""

import json
import logging
import uuid
from typing import Any, Dict, Optional

import db

logger = logging.getLogger("databricks-quest.services.audit")

_INSERT_SQL = """
    INSERT INTO event_audit_log
        (audit_id, event_id, actor_user_id, action,
         target_type, target_id, payload_json)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
"""


def record_audit(
    action: str,
    actor_user_id: Optional[str] = None,
    event_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    *,
    cursor: Optional[Any] = None,
) -> Optional[str]:
    """Insert an audit row and return its id.

    Args:
        action: Short verb describing what happened, e.g. ``"event.start"``.
        actor_user_id: Identity that performed the action.
        event_id: Event the action is scoped to, if any.
        target_type: Type of the affected object, e.g. ``"team"``.
        target_id: Id of the affected object.
        payload: Small, non-sensitive JSON summary of the change.
        cursor: An open DB cursor to write through (transactional mode). When
            given, the row joins the caller's transaction and write failures
            **propagate** so the whole transaction rolls back. When ``None``,
            the write is best-effort and failures return ``None``.
    """
    audit_id = f"aud_{uuid.uuid4().hex[:16]}"
    params = (
        audit_id,
        event_id,
        actor_user_id,
        action,
        target_type,
        target_id,
        json.dumps(payload) if payload is not None else None,
    )

    if cursor is not None:
        # Transactional mode: let failures propagate so the caller's transaction
        # rolls back atomically with the audited mutation.
        cursor.execute(_INSERT_SQL, params)
        return audit_id

    try:
        db.execute(_INSERT_SQL, params)
        return audit_id
    except Exception as exc:  # noqa: BLE001 - best-effort mode never breaks caller
        logger.warning("record_audit failed for action=%s: %s", action, exc)
        return None
