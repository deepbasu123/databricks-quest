"""Scoring service — turn a passing task into (at most) one base-points award.

Wraps :class:`ScoringRepository` with the idempotency-key policy from
``docs/06`` so the same task can only award base points once per scoring scope:

- standalone / master: the scope is the **team** (``team:{team_id}:...``);
- federation child: the scope is the **workspace** (``{workspace_id}:...``),
  reusing :func:`services.federation.deterministic_idempotency_key`, because a
  federated write carries ``workspace_id`` (one attendee per workspace) and no
  ``team_id`` at write time.

Manual host overrides are deliberately *not* routed through here — they are
separate scoring events that never collide with the base-points key.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from repositories.scoring import ScoringRepository
from services import federation as fed

logger = logging.getLogger("databricks-quest.services.scoring_service")


def base_points_idempotency_key(
    event_id: str,
    task_id: str,
    *,
    team_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    scoring_rule: str = "base",
) -> str:
    """Return the single-award key for a task's base points.

    Workspace scope wins when present (federation), else team scope. The two
    keyspaces are prefixed differently so they can never collide.
    """
    if workspace_id:
        return fed.deterministic_idempotency_key(
            workspace_id, event_id, task_id, scoring_rule
        )
    scope = team_id or "noteam"
    return f"team:{scope}:{event_id}:{task_id}:{scoring_rule}"


class ScoringService:
    def __init__(self, repo: Optional[ScoringRepository] = None):
        self._repo = repo or ScoringRepository()

    def award_task_base_points(
        self,
        *,
        event_id: str,
        task_id: str,
        points: int,
        attempt_id: str,
        quest_id: Optional[str] = None,
        team_id: Optional[str] = None,
        user_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Award a task's base points once. Idempotent across retries/writers.

        Returns the repository result: ``{"awarded": bool, "points": int,
        "scoring_event_id": str|None}``. Zero/negative point tasks short-circuit
        without writing a ledger row.
        """
        if points <= 0:
            return {"awarded": False, "points": 0, "scoring_event_id": None}

        # No scoring scope (no team and no workspace) → don't write an orphan
        # ledger row the leaderboard could never attribute. The attempt still
        # records its passing validation results; the host can attribute later
        # (standalone: register the player on a team; federation: import roster).
        if not team_id and not workspace_id:
            return {"awarded": False, "points": 0, "scoring_event_id": None}

        key = base_points_idempotency_key(
            event_id, task_id, team_id=team_id, workspace_id=workspace_id
        )
        return self._repo.insert_scoring_event(
            event_id=event_id,
            idempotency_key=key,
            points_delta=points,
            source_type="validation",
            source_id=attempt_id,
            reason="task_passed",
            task_id=task_id,
            quest_id=quest_id,
            team_id=team_id,
            user_id=user_id,
            workspace_id=workspace_id,
            created_by=created_by,
        )


default_scoring_service = ScoringService()
