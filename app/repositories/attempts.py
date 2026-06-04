"""Repository for task attempts and their validation results.

PR01 scope: read helpers for attempt/validation history. Submitting an attempt
runs through the validation engine and scoring path introduced in later PRs, so
the write path is a documented stub here.
"""

import logging
from typing import Any, Dict, List, Optional

import db

logger = logging.getLogger("databricks-quest.repositories.attempts")


class AttemptsRepository:
    """Access to the ``task_attempts`` / ``validation_results`` tables."""

    def get_attempt(self, attempt_id: str) -> Optional[Dict[str, Any]]:
        try:
            rows = db.execute_query(
                "SELECT * FROM task_attempts WHERE attempt_id = %s", (attempt_id,)
            )
            return rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_attempt failed: %s", exc)
            return None

    def list_attempts_for_team(
        self, event_id: str, team_id: str
    ) -> List[Dict[str, Any]]:
        try:
            return db.execute_query(
                """
                SELECT attempt_id, task_id, submitted_by, status,
                       submitted_at, completed_at, error_message
                FROM task_attempts
                WHERE event_id = %s AND team_id = %s
                ORDER BY submitted_at DESC
                """,
                (event_id, team_id),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_attempts_for_team failed: %s", exc)
            return []

    def list_validation_results(self, attempt_id: str) -> List[Dict[str, Any]]:
        try:
            return db.execute_query(
                """
                SELECT validation_result_id, validator_id, status, score_delta,
                       public_message, started_at, completed_at
                FROM validation_results
                WHERE attempt_id = %s
                ORDER BY completed_at ASC NULLS LAST
                """,
                (attempt_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_validation_results failed: %s", exc)
            return []

    # ── Mutations (deferred to later PRs) ────────────────────────────────────

    def create_attempt(self, *args, **kwargs):
        raise NotImplementedError("create_attempt is implemented in a later PR")

    def record_validation_result(self, *args, **kwargs):
        raise NotImplementedError(
            "record_validation_result is implemented in a later PR"
        )
