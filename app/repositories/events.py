"""Repository for events, hosts, teams, and participants.

PR01 scope: read helpers that the lobby/event APIs will build on, plus
documented stubs for the host-driven mutation paths (create event, manage
teams, lifecycle transitions) that arrive in later PRs.
"""

import logging
from typing import Any, Dict, List, Optional

import db

logger = logging.getLogger("databricks-quest.repositories.events")


class EventsRepository:
    """Access to the ``events`` / ``teams`` / ``participants`` tables."""

    def list_active_events(self) -> List[Dict[str, Any]]:
        """Return events that are visible to players (active/paused/frozen).

        Returns an empty list if Lakebase or the GameDay tables are unavailable.
        """
        try:
            return db.execute_query(
                """
                SELECT e.event_id, e.slug, e.title, e.status,
                       e.starts_at, e.ends_at, e.timezone,
                       p.title AS pack_title
                FROM events e
                LEFT JOIN quest_pack_versions pv
                       ON e.pack_version_id = pv.pack_version_id
                LEFT JOIN quest_packs p ON pv.pack_id = p.pack_id
                WHERE e.status IN ('active', 'paused', 'frozen')
                ORDER BY e.starts_at ASC NULLS LAST
                """
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_active_events failed: %s", exc)
            return []

    def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        """Return a single event row, or None if absent/unavailable."""
        try:
            rows = db.execute_query(
                "SELECT * FROM events WHERE event_id = %s", (event_id,)
            )
            return rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_event failed: %s", exc)
            return None

    def get_event_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        """Return a single event row by slug, or None if absent/unavailable."""
        try:
            rows = db.execute_query(
                "SELECT * FROM events WHERE slug = %s", (slug,)
            )
            return rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_event_by_slug failed: %s", exc)
            return None

    def list_event_hosts(self, event_id: str) -> List[Dict[str, Any]]:
        try:
            return db.execute_query(
                "SELECT user_id, role FROM event_hosts WHERE event_id = %s",
                (event_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_event_hosts failed: %s", exc)
            return []

    def list_teams(self, event_id: str) -> List[Dict[str, Any]]:
        try:
            return db.execute_query(
                "SELECT * FROM teams WHERE event_id = %s ORDER BY name ASC",
                (event_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_teams failed: %s", exc)
            return []

    def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
        try:
            rows = db.execute_query(
                "SELECT * FROM teams WHERE team_id = %s", (team_id,)
            )
            return rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_team failed: %s", exc)
            return None

    def get_team_for_user(
        self, event_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Resolve the team a user belongs to in an event (standalone path).

        Returns the full team row (including ``team_catalog``/``team_schema`` used
        as validator template variables), or None if the user is not on a team.
        """
        try:
            rows = db.execute_query(
                """
                SELECT t.*
                FROM teams t
                JOIN team_members tm ON tm.team_id = t.team_id
                JOIN participants p ON p.participant_id = tm.participant_id
                WHERE p.event_id = %s AND p.user_id = %s
                ORDER BY tm.joined_at ASC
                LIMIT 1
                """,
                (event_id, user_id),
            )
            return rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_team_for_user failed: %s", exc)
            return None

    def get_participant(self, event_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        try:
            rows = db.execute_query(
                "SELECT * FROM participants WHERE event_id = %s AND user_id = %s",
                (event_id, user_id),
            )
            return rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_participant failed: %s", exc)
            return None

    # ── Mutations (deferred to later PRs) ────────────────────────────────────

    def create_event(self, *args, **kwargs):
        raise NotImplementedError("create_event is implemented in a later PR")

    def set_status(self, *args, **kwargs):
        raise NotImplementedError("set_status is implemented in a later PR")

    def create_team(self, *args, **kwargs):
        raise NotImplementedError("create_team is implemented in a later PR")

    def register_participant(self, *args, **kwargs):
        raise NotImplementedError(
            "register_participant is implemented in a later PR"
        )
