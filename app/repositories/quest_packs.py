"""Repository for quest packs, versions, quests, tasks, validators, and hints.

PR01 scope: read helpers plus stubs for import/lint, which depend on the quest
pack schema parser and validator allowlist introduced in later PRs.
"""

import logging
from typing import Any, Dict, List, Optional

import db

logger = logging.getLogger("databricks-quest.repositories.quest_packs")


class QuestPacksRepository:
    """Access to the quest pack catalog tables."""

    def list_packs(self) -> List[Dict[str, Any]]:
        try:
            return db.execute_query(
                "SELECT * FROM quest_packs ORDER BY updated_at DESC"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_packs failed: %s", exc)
            return []

    def get_pack(self, pack_id: str) -> Optional[Dict[str, Any]]:
        try:
            rows = db.execute_query(
                "SELECT * FROM quest_packs WHERE pack_id = %s", (pack_id,)
            )
            return rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_pack failed: %s", exc)
            return None

    def list_versions(self, pack_id: str) -> List[Dict[str, Any]]:
        try:
            return db.execute_query(
                """
                SELECT pack_version_id, pack_id, version, content_hash,
                       status, imported_by, imported_at
                FROM quest_pack_versions
                WHERE pack_id = %s
                ORDER BY imported_at DESC
                """,
                (pack_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_versions failed: %s", exc)
            return []

    def list_quests(self, pack_version_id: str) -> List[Dict[str, Any]]:
        try:
            return db.execute_query(
                """
                SELECT quest_id, slug, title, category, difficulty,
                       sort_order, base_points
                FROM quests
                WHERE pack_version_id = %s
                ORDER BY sort_order ASC, title ASC
                """,
                (pack_version_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_quests failed: %s", exc)
            return []

    def list_tasks(self, quest_id: str) -> List[Dict[str, Any]]:
        try:
            return db.execute_query(
                """
                SELECT task_id, slug, title, objective, points,
                       sort_order, validation_mode
                FROM quest_tasks
                WHERE quest_id = %s
                ORDER BY sort_order ASC, title ASC
                """,
                (quest_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_tasks failed: %s", exc)
            return []

    # ── Mutations (deferred to later PRs) ────────────────────────────────────

    def import_pack(self, *args, **kwargs):
        raise NotImplementedError("import_pack is implemented in a later PR")

    def lint_pack(self, *args, **kwargs):
        raise NotImplementedError("lint_pack is implemented in a later PR")
