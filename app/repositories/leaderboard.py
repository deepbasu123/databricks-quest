"""Repository for event-scoped scoring and leaderboards.

Reads from the append-only ``scoring_events`` table and the derived
``team_scores`` / ``event_leaderboard`` views created by migration 001.
"""

import logging
from typing import Any, Dict, List, Optional

import db

logger = logging.getLogger("databricks-quest.repositories.leaderboard")


class LeaderboardRepository:
    """Access to scoring events and the event leaderboard views."""

    def get_team_leaderboard(self, event_id: str) -> List[Dict[str, Any]]:
        try:
            return db.execute_query(
                """
                SELECT event_id, team_id, display_name, total_points, rank,
                       last_scored_at
                FROM event_leaderboard
                WHERE event_id = %s
                ORDER BY rank ASC
                """,
                (event_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_team_leaderboard failed: %s", exc)
            return []

    def get_team_score(self, event_id: str, team_id: str) -> int:
        try:
            rows = db.execute_query(
                """
                SELECT COALESCE(total_points, 0) AS total_points
                FROM team_scores
                WHERE event_id = %s AND team_id = %s
                """,
                (event_id, team_id),
            )
            return int(rows[0]["total_points"]) if rows else 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_team_score failed: %s", exc)
            return 0

    def list_recent_scoring_events(
        self, event_id: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        try:
            return db.execute_query(
                """
                SELECT scoring_event_id, team_id, user_id, task_id,
                       source_type, points_delta, reason, created_at
                FROM scoring_events
                WHERE event_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (event_id, limit),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_recent_scoring_events failed: %s", exc)
            return []

    # ── Mutations (deferred to later PRs) ────────────────────────────────────

    def record_scoring_event(self, *args, **kwargs):
        raise NotImplementedError(
            "record_scoring_event is implemented in a later PR"
        )
