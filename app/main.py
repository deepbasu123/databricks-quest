import os
import logging
from datetime import datetime, date
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# Lakebase connection handling is centralized in db.py so the app and the
# migration runner share one implementation.
import db
from db import (
    LAKEBASE_HOST,
    LAKEBASE_DB,
    serialize,
    get_workspace_client,
    get_lakebase_connection,
    execute_query,
)
import config
from repositories import (
    QuestPacksRepository,
    EventsRepository,
    EventStateError,
    AttemptsRepository,
    LeaderboardRepository,
    FederationRepository,
    RosterImportError,
)
from repositories.events import attempts_open, JOINABLE_STATUSES
from services import quest_pack_loader
from services import federation as fed
from services import record_audit
from services.validation_engine import default_engine, aggregate_status
from services.scoring_service import default_scoring_service
from services.quest_pack_loader import QuestPackImportError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("databricks-quest")

app = FastAPI(title="Databricks Quest API")

if not LAKEBASE_HOST:
    logger.warning("LAKEBASE_HOST not set — database queries will fail until configured")
else:
    logger.info(f"Data source: Lakebase ({LAKEBASE_HOST}/{LAKEBASE_DB})")

logger.info("Quest role: %s", config.QUEST_ROLE)
if config.is_child():
    logger.info(
        "Federation child: workspace_id=%s event=%s → shared master Lakebase",
        config.QUEST_WORKSPACE_ID or "(unset)",
        config.QUEST_EVENT_SLUG or "(unset)",
    )

MISSION_DEFINITIONS = [
    # --- Getting Started ---
    {"id": "first_steps", "name": "First Steps", "description": "Record your first Databricks compute usage", "points": 25, "category": "Getting Started", "award_type": "one_time", "icon": "rocket"},
    # --- Data Engineering ---
    {"id": "job_creator", "name": "Job Creator", "description": "Create your first Lakeflow Job", "points": 100, "category": "Data Engineering", "award_type": "one_time", "icon": "briefcase"},
    {"id": "pipeline_builder", "name": "Pipeline Builder", "description": "Create your first Lakeflow Spark Declarative Pipeline", "points": 150, "category": "Data Engineering", "award_type": "one_time", "icon": "git-branch"},
    {"id": "pipeline_runner", "name": "Pipeline Runner", "description": "Complete your first successful pipeline update", "points": 200, "category": "Data Engineering", "award_type": "one_time", "icon": "play-circle"},
    {"id": "scheduler", "name": "Scheduler", "description": "Create a scheduled or CRON-triggered job", "points": 150, "category": "Data Engineering", "award_type": "one_time", "icon": "clock"},
    {"id": "auto_loader_pioneer", "name": "Auto Loader Pioneer", "description": "Use Auto Loader in a pipeline for streaming ingestion", "points": 250, "category": "Data Engineering", "award_type": "one_time", "icon": "upload-cloud"},
    {"id": "multi_task_orchestrator", "name": "Multi-Task Orchestrator", "description": "Create a workflow with 3+ tasks", "points": 200, "category": "Data Engineering", "award_type": "one_time", "icon": "git-branch"},
    {"id": "liquid_clustering", "name": "Liquid Clustering Adopter", "description": "Enable Liquid Clustering on a table", "points": 200, "category": "Data Engineering", "award_type": "one_time", "icon": "layers"},
    # --- Analytics ---
    {"id": "genie_creator", "name": "Genie Creator", "description": "Create your first AI/BI Genie space", "points": 200, "category": "Analytics", "award_type": "one_time", "icon": "sparkles"},
    {"id": "dashboard_designer", "name": "Dashboard Designer", "description": "Create your first Databricks Dashboard", "points": 150, "category": "Analytics", "award_type": "one_time", "icon": "layout-dashboard"},
    {"id": "data_explorer", "name": "Data Explorer", "description": "Execute 50+ SQL queries in a single week", "points": 150, "category": "Analytics", "award_type": "repeatable", "icon": "search"},
    {"id": "power_analyst", "name": "Power Analyst", "description": "Execute 200+ SQL queries in a single week", "points": 200, "category": "Analytics", "award_type": "repeatable", "icon": "bar-chart-2"},
    {"id": "alert_creator", "name": "Alert Creator", "description": "Create a SQL Alert with a schedule", "points": 150, "category": "Analytics", "award_type": "one_time", "icon": "bell"},
    {"id": "dashboard_publisher", "name": "Dashboard Publisher", "description": "Publish a dashboard shared with 3+ viewers", "points": 200, "category": "Analytics", "award_type": "one_time", "icon": "share-2"},
    # --- AI / ML ---
    {"id": "model_deployer", "name": "Model Deployer", "description": "Deploy a model to a serving endpoint", "points": 300, "category": "AI / ML", "award_type": "one_time", "icon": "cpu"},
    {"id": "ai_function_builder", "name": "AI Function Builder", "description": "Use ai_query() in a SQL statement", "points": 250, "category": "AI / ML", "award_type": "one_time", "icon": "sparkles"},
    {"id": "vector_search_pioneer", "name": "Vector Search Pioneer", "description": "Create a Vector Search index", "points": 200, "category": "AI / ML", "award_type": "one_time", "icon": "search"},
    {"id": "mlflow_experimenter", "name": "MLflow Experimenter", "description": "Log 10+ MLflow experiment runs", "points": 150, "category": "AI / ML", "award_type": "one_time", "icon": "flask-conical"},
    # --- Streaming ---
    {"id": "stream_starter", "name": "Stream Starter", "description": "Run a Structured Streaming job for 24+ hours", "points": 250, "category": "Streaming", "award_type": "one_time", "icon": "radio"},
    # --- Consumption (repeatable, weekly) ---
    {"id": "sql_analyst", "name": "SQL Analyst", "description": "Consume 50+ SQL Warehouse DBUs in a month", "points": 100, "category": "Consumption", "award_type": "repeatable", "icon": "database"},
    {"id": "job_runner", "name": "Job Runner", "description": "Consume 50+ Jobs Compute DBUs in a month", "points": 100, "category": "Consumption", "award_type": "repeatable", "icon": "play"},
    {"id": "ml_practitioner", "name": "ML Practitioner", "description": "Consume any Model Serving DBUs in a month", "points": 150, "category": "Consumption", "award_type": "repeatable", "icon": "brain"},
    {"id": "dlt_operator", "name": "Pipeline Operator", "description": "Consume 50+ DLT DBUs in a month", "points": 100, "category": "Consumption", "award_type": "repeatable", "icon": "activity"},
    # --- Consumption milestones (one-time) ---
    {"id": "dbu_100", "name": "First 100 DBUs", "description": "Reach 100 lifetime DBUs consumed", "points": 50, "category": "Consumption", "award_type": "one_time", "icon": "zap"},
    {"id": "dbu_1k", "name": "1K DBU Club", "description": "Reach 1,000 lifetime DBUs consumed", "points": 200, "category": "Consumption", "award_type": "one_time", "icon": "zap"},
    {"id": "dbu_10k", "name": "10K DBU Club", "description": "Reach 10,000 lifetime DBUs consumed", "points": 500, "category": "Consumption", "award_type": "one_time", "icon": "zap"},
    {"id": "dbu_100k", "name": "100K DBU Club", "description": "Reach 100,000 lifetime DBUs consumed", "points": 1000, "category": "Consumption", "award_type": "one_time", "icon": "trophy"},
    # --- Engagement ---
    {"id": "consistent_operator", "name": "Consistent Operator", "description": "Run pipelines or jobs on 7 distinct days within 30 days", "points": 300, "category": "Engagement", "award_type": "repeatable", "icon": "calendar-check"},
    {"id": "daily_driver", "name": "Daily Driver", "description": "Active on 20+ days in a 30-day window", "points": 400, "category": "Engagement", "award_type": "repeatable", "icon": "calendar"},
    {"id": "cross_product_champion", "name": "Cross-Product Champion", "description": "Use 6+ distinct Databricks products in a month", "points": 500, "category": "Engagement", "award_type": "repeatable", "icon": "award"},
    # --- Governance ---
    {"id": "uc_publisher", "name": "Unity Catalog Publisher", "description": "Share a table across schemas", "points": 150, "category": "Governance", "award_type": "one_time", "icon": "share"},
]

# Consumption points: 1 point per 10 DBUs consumed, scored weekly
CONSUMPTION_POINTS_RATIO = 10  # DBUs per point

BADGE_DEFINITIONS = [
    {"id": "pipeline_craftsman", "name": "Pipeline Craftsman", "description": "Complete 5 pipeline-related missions", "icon": "wrench", "required_missions": 5, "mission_filter": ["pipeline_builder", "pipeline_runner", "auto_loader_pioneer", "consistent_operator", "scheduler"]},
    {"id": "platform_explorer", "name": "Platform Explorer", "description": "Use 4+ distinct Databricks product areas", "icon": "compass", "required_products": 4},
    {"id": "consistent_contributor", "name": "Consistent Contributor", "description": "Maintain a 14-day activity streak", "icon": "flame", "required_streak": 14},
    {"id": "ai_pioneer", "name": "AI Pioneer", "description": "Complete 3 AI/ML missions", "icon": "brain", "required_missions": 3, "mission_filter": ["model_deployer", "ai_function_builder", "vector_search_pioneer", "mlflow_experimenter"]},
    {"id": "consumption_king", "name": "Consumption King", "description": "Reach the 10K DBU Club milestone", "icon": "crown", "required_milestone": "dbu_10k"},
    {"id": "full_stack", "name": "Full Stack", "description": "Complete missions in 5+ different categories", "icon": "layers", "required_categories": 5},
]

LEVEL_THRESHOLDS = [
    ("Elite", 5000),
    ("Platinum", 2000),
    ("Gold", 800),
    ("Silver", 300),
    ("Bronze", 0),
]


def get_level(points: int) -> str:
    for name, threshold in LEVEL_THRESHOLDS:
        if points >= threshold:
            return name
    return "Bronze"


def get_level_progress(points: int) -> dict:
    level = get_level(points)
    for i, (name, threshold) in enumerate(LEVEL_THRESHOLDS):
        if points >= threshold:
            next_threshold = LEVEL_THRESHOLDS[i - 1][1] if i > 0 else threshold + 1000
            return {
                "level": name,
                "current_points": points,
                "level_floor": threshold,
                "level_ceiling": next_threshold,
                "progress_pct": min(100, int((points - threshold) / max(1, next_threshold - threshold) * 100)),
            }
    return {"level": "Bronze", "current_points": 0, "level_floor": 0, "level_ceiling": 300, "progress_pct": 0}


def get_user_email(request: Request) -> str:
    email = request.headers.get("X-Forwarded-Email", "")
    if not email:
        email = request.headers.get("X-Forwarded-User", "")
    if not email:
        email = os.getenv("QUEST_DEFAULT_USER", "unknown@example.com")
    return email


# Optional host allowlist for GameDay host endpoints. When QUEST_HOST_ALLOWLIST
# is set (comma-separated emails) only those users may call /api/host/*; when
# unset the endpoints are open, matching today's /api/admin behaviour. This is
# the single chokepoint to replace with the full event role model in a later PR.
QUEST_HOST_ALLOWLIST = [
    e.strip().lower() for e in os.getenv("QUEST_HOST_ALLOWLIST", "").split(",") if e.strip()
]


def _ensure_event_mode() -> None:
    """Gate GameDay/Event Mode surfaces. 404 when Event Mode is not enabled.

    Event Mode is opt-in (``QUEST_EVENT_MODE`` / ``--event-mode``; implied by the
    master/child roles). When off, the deployment is the legacy adoption app and
    every GameDay endpoint behaves as if it does not exist.
    """
    if not config.event_mode_enabled():
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "EVENT_MODE_DISABLED",
                    "message": "Event Mode is not enabled on this deployment.",
                }
            },
        )


def require_event_mode() -> None:
    """FastAPI dependency form of :func:`_ensure_event_mode`."""
    _ensure_event_mode()


def require_host(request: Request) -> str:
    """FastAPI dependency: resolve the user and enforce the host allowlist.

    Also gates on Event Mode — every ``/api/host/*`` surface is GameDay-only, so
    a legacy (Event-Mode-off) deployment 404s here.
    """
    _ensure_event_mode()
    user = get_user_email(request)
    if QUEST_HOST_ALLOWLIST and user.lower() not in QUEST_HOST_ALLOWLIST:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "FORBIDDEN", "message": "Host role required."}},
        )
    return user


def require_master_host(request: Request) -> str:
    """Host dependency that additionally forbids the child role.

    Roster / workspace-health endpoints are master-side concerns (children have
    only INSERT on the shared facts and cannot create teams/participants). They
    are available on ``master`` and ``standalone`` (for local testing) but
    return 404 on a ``child`` deployment, so the same binary exposes the right
    surface per role.
    """
    if config.is_child():
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Endpoint not available in child role."}},
        )
    return require_host(request)


quest_packs_repo = QuestPacksRepository()
events_repo = EventsRepository()
attempts_repo = AttemptsRepository()
leaderboard_repo = LeaderboardRepository()
federation_repo = FederationRepository()


@app.on_event("startup")
async def _federation_startup() -> None:
    """Child role: record this workspace's presence in the shared DB once."""
    if not config.event_mode_enabled():
        return
    try:
        fed.startup_checkin()
    except Exception as exc:  # noqa: BLE001 - never block startup
        logger.warning("federation startup check-in skipped: %s", exc)


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    try:
        result = execute_query("SELECT 1 AS ok")
        db_ok = len(result) > 0
    except Exception:
        db_ok = False

    # GameDay migration status — empty when migrations have not run yet or
    # Lakebase is unavailable, so health never fails because of this.
    migrations = db.applied_migrations()

    return {
        "status": "ok" if db_ok else "degraded",
        "db_connected": db_ok,
        "migrations_applied": migrations,
        "migrations_count": len(migrations),
        "event_mode": config.event_mode_enabled(),
        "role": config.QUEST_ROLE,
        "federation": config.summary(),
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/api/profile")
async def get_profile(request: Request):
    user = get_user_email(request)
    try:
        profiles = execute_query(
            "SELECT * FROM user_profile_snapshot WHERE user_id = %s", (user,)
        )
        if profiles:
            profile = profiles[0]
            profile["level_progress"] = get_level_progress(int(profile.get("total_points", 0)))
            badges = execute_query(
                "SELECT * FROM badges WHERE user_id = %s ORDER BY earned_at DESC", (user,)
            )
            profile["badges"] = badges
            return profile
        return _empty_profile(user)
    except Exception as e:
        logger.warning(f"Profile fetch error: {e}")
        result = _empty_profile(user)
        result["setup_required"] = True
        return result


def _empty_profile(user: str) -> dict:
    return {
        "user_id": user,
        "display_name": user.split("@")[0] if "@" in user else user,
        "total_points": 0,
        "level": "Bronze",
        "level_progress": get_level_progress(0),
        "current_streak": 0,
        "max_streak": 0,
        "badge_count": 0,
        "missions_completed": 0,
        "distinct_products_used": 0,
        "badges": [],
    }


@app.get("/api/missions")
async def get_missions(request: Request):
    user = get_user_email(request)
    missions = [m.copy() for m in MISSION_DEFINITIONS]
    try:
        completions = execute_query(
            "SELECT mission_id, completed_at, points_awarded FROM mission_completions WHERE user_id = %s",
            (user,),
        )
        completed_map = {c["mission_id"]: c for c in completions}
    except Exception:
        completed_map = {}

    for m in missions:
        if m["id"] in completed_map:
            m["status"] = "completed"
            m["completed_at"] = completed_map[m["id"]].get("completed_at")
        else:
            m["status"] = "available"
    return {"missions": missions, "user_id": user}


@app.get("/api/leaderboard")
async def get_leaderboard(period: str = "all"):
    try:
        if period == "weekly":
            order_col = "weekly_rank"
        elif period == "monthly":
            order_col = "monthly_rank"
        else:
            order_col = "all_time_rank"

        rows = execute_query(
            f"""
            SELECT user_id, display_name, total_points, weekly_points, monthly_points,
                   level, all_time_rank, weekly_rank, monthly_rank
            FROM leaderboard
            ORDER BY {order_col} ASC
            LIMIT 10
            """
        )
        return {"leaderboard": rows, "period": period}
    except Exception as e:
        logger.warning(f"Leaderboard error: {e}")
        return {"leaderboard": [], "period": period}


@app.get("/api/notifications")
async def get_notifications(request: Request):
    user = get_user_email(request)
    try:
        rows = execute_query(
            """
            SELECT notification_type, title, message, mission_id, points, created_at
            FROM notifications
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT 20
            """,
            (user,),
        )
        return {"notifications": rows}
    except Exception:
        return {"notifications": []}


@app.get("/api/admin/stats")
async def get_admin_stats():
    try:
        user_count = execute_query("SELECT COUNT(DISTINCT user_id) AS cnt FROM user_profile_snapshot")
        mission_count = execute_query("SELECT COUNT(*) AS cnt FROM mission_completions")
        top_missions = execute_query(
            """
            SELECT mission_id, mission_name, COUNT(*) AS completions
            FROM mission_completions
            GROUP BY mission_id, mission_name
            ORDER BY completions DESC
            """
        )
        level_dist = execute_query(
            """
            SELECT level, COUNT(*) AS cnt
            FROM user_profile_snapshot
            GROUP BY level
            ORDER BY cnt DESC
            """
        )
        latest_run = execute_query(
            "SELECT MAX(updated_at) AS last_refresh FROM user_profile_snapshot"
        )
        return {
            "total_users": user_count[0]["cnt"] if user_count else 0,
            "total_mission_completions": mission_count[0]["cnt"] if mission_count else 0,
            "top_missions": top_missions,
            "level_distribution": level_dist,
            "last_refresh": latest_run[0]["last_refresh"] if latest_run else None,
        }
    except Exception as e:
        logger.warning(f"Admin stats error: {e}")
        return {
            "total_users": 0,
            "total_mission_completions": 0,
            "top_missions": [],
            "level_distribution": [],
            "last_refresh": None,
            "setup_required": True,
        }


@app.get("/api/admin/pipeline-status")
async def get_pipeline_status():
    try:
        latest = execute_query("SELECT MAX(updated_at) AS last_run FROM user_profile_snapshot")
        last_run = latest[0]["last_run"] if latest and latest[0].get("last_run") else None
        total = execute_query("SELECT COUNT(*) AS cnt FROM user_points_fact")
        return {
            "status": "healthy" if last_run else "not_initialized",
            "last_run": last_run,
            "total_events_scored": total[0]["cnt"] if total else 0,
        }
    except Exception:
        return {"status": "not_initialized", "last_run": None, "total_events_scored": 0}


# ---------------------------------------------------------------------------
# Host / admin — Quest Packs (PR02)
# ---------------------------------------------------------------------------

class QuestPackPayload(BaseModel):
    manifest_yaml: str


@app.post("/api/host/quest-packs/lint")
async def host_lint_quest_pack(body: QuestPackPayload, user: str = Depends(require_host)):
    """Lint a quest pack manifest without persisting anything."""
    return quest_pack_loader.lint_text(body.manifest_yaml)


@app.post("/api/host/quest-packs/import")
async def host_import_quest_pack(body: QuestPackPayload, user: str = Depends(require_host)):
    """Lint and import a quest pack manifest as a new immutable version."""
    try:
        return quest_pack_loader.import_text(body.manifest_yaml, actor=user)
    except QuestPackImportError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {"code": "QUEST_PACK_INVALID", "message": exc.message},
                "lint": exc.lint,
            },
        )


@app.get("/api/host/quest-packs")
async def host_list_quest_packs(user: str = Depends(require_host)):
    """List imported quest packs."""
    return {"quest_packs": quest_packs_repo.list_packs()}


@app.get("/api/host/quest-packs/{pack_id}")
async def host_get_quest_pack(pack_id: str, user: str = Depends(require_host)):
    """Get a quest pack with its versions and per-version content counts."""
    detail = quest_packs_repo.get_pack_detail(pack_id)
    if not detail:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Quest pack not found."}},
        )
    return detail


# ---------------------------------------------------------------------------
# Events, teams, participants — lifecycle (PR04)
# ---------------------------------------------------------------------------
# Player endpoints (list/lobby/join) are Event-Mode-gated and open to any
# authenticated user. Host endpoints (create event/teams, import participants,
# lifecycle transitions) go through require_host. Every mutation writes an
# audit row. The event status state machine lives in repositories.events.


class CreateEventPayload(BaseModel):
    title: str
    pack_version_id: str
    slug: Optional[str] = None
    description: Optional[str] = None
    timezone: str = "UTC"
    mode: str = "gameday"


class CreateTeamPayload(BaseModel):
    name: str
    display_name: Optional[str] = None
    color: Optional[str] = None
    team_catalog: Optional[str] = None
    team_schema: Optional[str] = None


class JoinEventPayload(BaseModel):
    display_name: Optional[str] = None
    team_id: Optional[str] = None
    team_name: Optional[str] = None


class ParticipantImportPayload(BaseModel):
    participants: List[Dict[str, Any]]


def _slugify(text: str) -> str:
    import re

    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s or f"event-{uuid_hex8()}"


def uuid_hex8() -> str:
    import uuid as _uuid

    return _uuid.uuid4().hex[:8]


def _event_state_error(exc: EventStateError) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={"error": {"code": exc.code, "message": str(exc)}},
    )


def _event_public(ev: Dict[str, Any]) -> Dict[str, Any]:
    """Trim an event row to the fields players/hosts need."""
    keys = (
        "event_id", "slug", "title", "description", "status", "mode",
        "starts_at", "ends_at", "timezone", "scoring_frozen_at",
        "pack_version_id", "created_by", "created_at",
    )
    return {k: ev.get(k) for k in keys}


@app.get("/api/events")
async def list_events(request: Request, _: None = Depends(require_event_mode)):
    """Player-facing list of joinable/visible events with team counts."""
    return {"events": events_repo.list_player_events()}


@app.get("/api/events/{event_id}")
async def get_event_lobby(event_id: str, request: Request, _: None = Depends(require_event_mode)):
    """Event lobby: event header, teams (with counts), your participant/team, counts."""
    resolved_event_id = _resolve_event_or_404(event_id)
    ev = events_repo.get_event(resolved_event_id)
    user = get_user_email(request)

    participant = events_repo.get_participant(resolved_event_id, user)
    team = events_repo.get_team_for_user(resolved_event_id, user)
    return {
        "event": _event_public(ev),
        "joinable": ev["status"] in JOINABLE_STATUSES,
        "attempts_open": attempts_open(ev["status"]),
        "is_host": events_repo.is_host(resolved_event_id, user),
        "teams": events_repo.list_teams_with_counts(resolved_event_id),
        "counts": events_repo.event_counts(resolved_event_id, ev.get("pack_version_id")),
        "you": {
            "joined": participant is not None,
            "participant_id": participant.get("participant_id") if participant else None,
            "team_id": team.get("team_id") if team else None,
            "team_name": team.get("display_name") or team.get("name") if team else None,
        },
    }


@app.post("/api/events/{event_id}/join")
async def join_event(
    event_id: str,
    body: JoinEventPayload,
    request: Request,
    _: None = Depends(require_event_mode),
):
    """Self-register the caller as a participant; optionally join a named team."""
    resolved_event_id = _resolve_event_or_404(event_id)
    ev = events_repo.get_event(resolved_event_id)
    user = get_user_email(request)
    if ev["status"] not in JOINABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={"error": {"code": "NOT_JOINABLE", "message": "This event is not open for joining right now."}},
        )

    try:
        participant = events_repo.register_participant(
            event_id=resolved_event_id, user_id=user, display_name=body.display_name, email=user
        )
        team = None
        if body.team_id or body.team_name:
            team = (
                events_repo.get_team(body.team_id)
                if body.team_id
                else _find_team_by_name(resolved_event_id, body.team_name)
            )
            if not team or team.get("event_id") != resolved_event_id:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": "TEAM_NOT_FOUND", "message": "That team is not part of this event."}},
                )
            events_repo.assign_team(team["team_id"], participant["participant_id"])
    except EventStateError as exc:
        raise _event_state_error(exc)

    record_audit(
        action="event.join",
        actor_user_id=user,
        event_id=resolved_event_id,
        target_type="participant",
        target_id=participant["participant_id"],
        payload={"team_id": team["team_id"] if team else None},
    )
    return {
        "joined": True,
        "participant_id": participant["participant_id"],
        "team_id": team["team_id"] if team else None,
        "team_name": (team.get("display_name") or team.get("name")) if team else None,
    }


def _find_team_by_name(event_id: str, name: Optional[str]) -> Optional[Dict[str, Any]]:
    if not name:
        return None
    for t in events_repo.list_teams(event_id):
        if t.get("name") == name or t.get("display_name") == name:
            return t
    return None


@app.post("/api/host/events")
async def host_create_event(body: CreateEventPayload, user: str = Depends(require_host)):
    """Create a draft event from an imported pack version (creator becomes owner-host)."""
    slug = (body.slug or _slugify(body.title)).strip().lower()
    try:
        ev = events_repo.create_event(
            slug=slug,
            title=body.title,
            pack_version_id=body.pack_version_id,
            created_by=user,
            description=body.description,
            mode=body.mode,
            timezone=body.timezone,
        )
    except EventStateError as exc:
        raise _event_state_error(exc)

    record_audit(
        action="event.create",
        actor_user_id=user,
        event_id=ev["event_id"],
        target_type="event",
        target_id=ev["event_id"],
        payload={"slug": ev["slug"], "pack_version_id": body.pack_version_id},
    )
    return {"event": _event_public(ev)}


@app.post("/api/host/events/{event_id}/teams")
async def host_create_team(
    event_id: str, body: CreateTeamPayload, user: str = Depends(require_host)
):
    """Create a team in an event."""
    resolved_event_id = _resolve_event_or_404(event_id)
    try:
        team = events_repo.create_team(
            event_id=resolved_event_id,
            name=body.name,
            display_name=body.display_name,
            color=body.color,
            team_catalog=body.team_catalog,
            team_schema=body.team_schema,
        )
    except EventStateError as exc:
        raise _event_state_error(exc)

    record_audit(
        action="team.create",
        actor_user_id=user,
        event_id=resolved_event_id,
        target_type="team",
        target_id=team["team_id"],
        payload={"name": team["name"]},
    )
    return {"team": team}


@app.post("/api/host/events/{event_id}/participants/import")
async def host_import_participants(
    event_id: str, body: ParticipantImportPayload, user: str = Depends(require_host)
):
    """Bulk-register participants with optional team assignment (idempotent)."""
    resolved_event_id = _resolve_event_or_404(event_id)
    try:
        result = events_repo.import_participants(resolved_event_id, body.participants)
    except EventStateError as exc:
        raise _event_state_error(exc)

    record_audit(
        action="participants.import",
        actor_user_id=user,
        event_id=resolved_event_id,
        target_type="event",
        target_id=resolved_event_id,
        payload=result,
    )
    return result


def _transition_endpoint(target: str):
    async def _do(event_id: str, request: Request, user: str = Depends(require_host)):
        resolved_event_id = _resolve_event_or_404(event_id)
        try:
            ev = events_repo.set_status(resolved_event_id, target, user)
        except EventStateError as exc:
            raise _event_state_error(exc)
        record_audit(
            action=f"event.{target}",
            actor_user_id=user,
            event_id=resolved_event_id,
            target_type="event",
            target_id=resolved_event_id,
            payload={"status": ev["status"]},
        )
        return {"event": _event_public(ev)}

    return _do


# Map the spec's verbs to target statuses. Each writes an audit row.
app.add_api_route("/api/host/events/{event_id}/start", _transition_endpoint("active"), methods=["POST"])
app.add_api_route("/api/host/events/{event_id}/pause", _transition_endpoint("paused"), methods=["POST"])
app.add_api_route("/api/host/events/{event_id}/freeze", _transition_endpoint("frozen"), methods=["POST"])
app.add_api_route("/api/host/events/{event_id}/complete", _transition_endpoint("completed"), methods=["POST"])
app.add_api_route("/api/host/events/{event_id}/ready", _transition_endpoint("ready"), methods=["POST"])
app.add_api_route("/api/host/events/{event_id}/archive", _transition_endpoint("archived"), methods=["POST"])


# ---------------------------------------------------------------------------
# Federation — multi-workspace GameDay (ADR_006)
# ---------------------------------------------------------------------------
# These endpoints ship in every deployment; role only changes which are
# meaningfully active. The child read endpoints work everywhere (standalone
# returns an empty/standalone shape); the host roster/health endpoints 404 on
# a child deployment via require_master_host.


class RosterImportPayload(BaseModel):
    csv: str


def _resolve_event_or_404(event_id_or_slug: str) -> str:
    """Accept either an event_id or a slug and return the canonical event_id."""
    ev = events_repo.get_event(event_id_or_slug) or events_repo.get_event_by_slug(
        event_id_or_slug
    )
    if not ev:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Event not found."}},
        )
    return ev["event_id"]


@app.get("/api/federation/status")
async def federation_status(request: Request, _: None = Depends(require_event_mode)):
    """Role + this workspace's own team mapping (child 'your rank' context).

    Includes a DB-connection health flag the child UI uses for its indicator.
    """
    user = get_user_email(request)
    status = fed.child_status(submitted_by=user)
    status["submitted_by"] = user
    status["event_mode"] = config.event_mode_enabled()
    try:
        status["db_connected"] = db.healthcheck()
    except Exception:  # noqa: BLE001
        status["db_connected"] = False
    return status


@app.get("/api/federation/leaderboard")
async def federation_leaderboard(
    request: Request,
    event: Optional[str] = None,
    _: None = Depends(require_event_mode),
):
    """Event-wide leaderboard from the shared view + this workspace's own team.

    Resolves the event from the query param, else the configured event slug.
    Returns ``{ leaderboard: [...], you: {...}|null, mapped: bool }`` so the
    child UI can render the overall standings and highlight its team's rank.
    """
    user = get_user_email(request)
    event_id = fed.resolve_event_id(event) if event else fed.resolve_event_id()
    if event and not event_id:
        # Caller passed an explicit id/slug — try it directly as an id.
        ev = events_repo.get_event(event)
        event_id = ev["event_id"] if ev else None
    if not event_id:
        return {"leaderboard": [], "you": None, "mapped": False, "event_id": None}

    rows = leaderboard_repo.get_team_leaderboard(event_id)
    you = None
    mapped = False
    if config.QUEST_WORKSPACE_ID:
        identity = federation_repo.resolve_identity(
            event_id, config.QUEST_WORKSPACE_ID, user
        )
        if identity and identity.get("team_id"):
            mapped = True
            team_id = identity["team_id"]
            you = next((r for r in rows if r.get("team_id") == team_id), None)
            if you is None:
                # Mapped but no points yet — surface the team with a null rank.
                you = {
                    "event_id": event_id,
                    "team_id": team_id,
                    "display_name": identity.get("team_display_name"),
                    "total_points": 0,
                    "rank": None,
                }
    return {
        "leaderboard": rows,
        "you": you,
        "mapped": mapped,
        "event_id": event_id,
        "workspace_id": config.QUEST_WORKSPACE_ID or None,
    }


@app.post("/api/host/events/{event_id}/roster/import")
async def host_import_roster(
    event_id: str,
    body: RosterImportPayload,
    user: str = Depends(require_master_host),
):
    """Import a roster CSV: pre-create teams/participants + identity map.

    CSV columns (aliases accepted): workspace_id|workspace_host, lab_user_email,
    team_name, optional display_name, real_email. Re-import is idempotent.
    """
    resolved = _resolve_event_or_404(event_id)
    try:
        result = federation_repo.import_roster(resolved, body.csv)
    except RosterImportError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "ROSTER_INVALID", "message": str(exc)}},
        )
    from services import record_audit

    record_audit(
        action="roster.import",
        actor_user_id=user,
        event_id=resolved,
        target_type="event",
        target_id=resolved,
        payload={k: result[k] for k in ("rows", "teams_created", "participants_created", "identities_mapped")},
    )
    return result


@app.get("/api/host/events/{event_id}/workspaces")
async def host_event_workspaces(event_id: str, user: str = Depends(require_master_host)):
    """Per-workspace health: check-ins, write counts, validation pass rate."""
    resolved = _resolve_event_or_404(event_id)
    return {"workspaces": federation_repo.list_event_workspaces(resolved)}


@app.get("/api/host/events/{event_id}/identities/unmapped")
async def host_unmapped_identities(event_id: str, user: str = Depends(require_master_host)):
    """Federated scoring rows not yet attributable to a team, for reconciliation."""
    resolved = _resolve_event_or_404(event_id)
    return {"unmapped": federation_repo.list_unmapped_identities(resolved)}


# ---------------------------------------------------------------------------
# Player gameplay — attempt submission + validation (PR03)
# ---------------------------------------------------------------------------
# One submission path serves every role. Standalone/master resolve the team via
# team membership; a federation child resolves it via the identity map (and
# leaves team_id NULL, stamping workspace_id so the master attributes it later).
# Validators run through services.validation_engine; a passing task awards base
# points exactly once via services.scoring_service.


class AttemptSubmissionPayload(BaseModel):
    submission: Optional[Dict[str, Any]] = None


def _build_validator_variables(
    event_id: str, team_row: Optional[Dict[str, Any]], team_id: Optional[str]
) -> Dict[str, Any]:
    """Server-resolved template variables. Players never supply these.

    Only these names may appear in a validator's ``${...}`` slots; the safety
    layer rejects any other slot, so a player can't redirect a check at another
    team's resources.
    """
    variables: Dict[str, Any] = {"event_id": event_id}
    if team_id:
        variables["team_id"] = team_id
    if team_row:
        if team_row.get("team_catalog"):
            variables["team_catalog"] = team_row["team_catalog"]
        if team_row.get("team_schema"):
            variables["team_schema"] = team_row["team_schema"]
    return variables


def _resolve_attempt_identity(event_id: str, user: str) -> Dict[str, Any]:
    """Resolve (team_id, team_row, workspace_id) for the submitting user."""
    if config.is_child():
        workspace_id = config.QUEST_WORKSPACE_ID or None
        team_id = None
        team_row = None
        if workspace_id:
            identity = federation_repo.resolve_identity(event_id, workspace_id, user)
            team_id = identity.get("team_id") if identity else None
            if team_id:
                team_row = events_repo.get_team(team_id)
        return {"team_id": team_id, "team_row": team_row, "workspace_id": workspace_id}

    team_row = events_repo.get_team_for_user(event_id, user)
    return {
        "team_id": team_row["team_id"] if team_row else None,
        "team_row": team_row,
        "workspace_id": None,
    }


def _closed_event_message(status: str) -> str:
    """Player-safe reason a submission is blocked for a non-active event."""
    return {
        "paused": "This event is paused. Your host will resume it shortly.",
        "frozen": "Scoring is frozen for the finale — submissions are closed.",
        "completed": "This event has ended. Thanks for playing!",
        "archived": "This event has been archived.",
        "draft": "This event hasn't opened yet.",
        "ready": "This event hasn't started yet. Hang tight!",
    }.get(status, "Submissions are closed right now.")


def _attempt_message(status: str, points: int, already_awarded: bool) -> str:
    """Player-safe summary message for an attempt's terminal status."""
    if status == "passed":
        if already_awarded:
            return "Already completed — your team keeps its points."
        if points > 0:
            return f"Task complete! +{points} points."
        return "Task complete!"
    if status == "failed":
        return "Not passed yet. Review the success criteria and try again."
    if status == "manual":
        return "Submitted for host review. A facilitator will confirm this task."
    if status == "error":
        return "We couldn't verify this task right now. Please try again shortly."
    return "Submitted."


@app.post("/api/events/{event_id}/tasks/{task_id}/attempts")
async def submit_attempt(
    event_id: str,
    task_id: str,
    body: AttemptSubmissionPayload,
    request: Request,
    _: None = Depends(require_event_mode),
):
    """Submit an attempt for a task: validate, persist, and score on pass.

    Accepts an ``event_id`` or slug. Returns a player-safe result; raw validator
    diagnostics are persisted to ``validation_results.private_message`` for the
    host, never returned to the player.
    """
    user = get_user_email(request)
    resolved_event_id = _resolve_event_or_404(event_id)

    # Event lifecycle gate: only an active event accepts new attempts. Paused,
    # frozen, completed, draft/ready, and archived all block with a safe message.
    ev = events_repo.get_event(resolved_event_id)
    if ev and not attempts_open(ev["status"]):
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "EVENT_NOT_ACTIVE",
                    "message": _closed_event_message(ev["status"]),
                }
            },
        )

    task = quest_packs_repo.get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Task not found."}},
        )

    identity = _resolve_attempt_identity(resolved_event_id, user)
    team_id = identity["team_id"]
    workspace_id = identity["workspace_id"]
    variables = _build_validator_variables(resolved_event_id, identity["team_row"], team_id)

    try:
        attempt_id = attempts_repo.create_attempt(
            event_id=resolved_event_id,
            task_id=task_id,
            submitted_by=user,
            submission=body.submission,
            team_id=team_id,
            workspace_id=workspace_id,
            status="running",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("create_attempt failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": "ATTEMPT_FAILED", "message": "Could not record your attempt. Try again shortly."}},
        )

    submission = body.submission or {}
    validators = quest_packs_repo.list_validators(task_id)

    outcomes = []
    public_results = []
    for v in validators:
        outcome = default_engine.run_validator(v, submission, variables)
        outcomes.append(outcome)
        try:
            attempts_repo.record_validation_result(
                attempt_id=attempt_id,
                validator_id=v["validator_id"],
                status=outcome.status,
                score_delta=outcome.score_delta,
                public_message=outcome.public_message,
                private_message=outcome.private_message,
                evidence=outcome.evidence,
                workspace_id=workspace_id,
            )
        except Exception as exc:  # noqa: BLE001 - persistence best-effort per result
            logger.warning("record_validation_result failed: %s", exc)
        public_results.append({"status": outcome.status, "message": outcome.public_message})

    status = aggregate_status(outcomes) if validators else "error"

    points_awarded = 0
    already_awarded = False
    if status == "passed":
        task_points = int(task.get("points") or 0)
        award = default_scoring_service.award_task_base_points(
            event_id=resolved_event_id,
            task_id=task_id,
            points=task_points,
            attempt_id=attempt_id,
            quest_id=task.get("quest_id"),
            team_id=team_id,
            user_id=user,
            workspace_id=workspace_id,
            created_by=user,
        )
        points_awarded = award["points"]
        already_awarded = bool(
            (not award["awarded"]) and task_points > 0 and (team_id or workspace_id)
        )

    error_sentinel = "validation_error" if status == "error" else None
    try:
        attempts_repo.set_status(attempt_id, status, error_sentinel)
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_status failed: %s", exc)

    record_audit(
        action="attempt.submit",
        actor_user_id=user,
        event_id=resolved_event_id,
        target_type="task",
        target_id=task_id,
        payload={
            "attempt_id": attempt_id,
            "status": status,
            "points_awarded": points_awarded,
            "validators": len(validators),
            "workspace_id": workspace_id,
        },
    )

    return {
        "attempt_id": attempt_id,
        "status": status,
        "message": _attempt_message(status, points_awarded, already_awarded),
        "points_awarded": points_awarded,
        "already_awarded": already_awarded,
        "results": public_results,
        "team_id": team_id,
    }


@app.get("/api/events/{event_id}/attempts/{attempt_id}")
async def get_attempt_status(
    event_id: str,
    attempt_id: str,
    request: Request,
    _: None = Depends(require_event_mode),
):
    """Return an attempt's status + player-safe per-validator results."""
    resolved_event_id = _resolve_event_or_404(event_id)
    attempt = attempts_repo.get_attempt(attempt_id)
    if not attempt or attempt.get("event_id") != resolved_event_id:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Attempt not found."}},
        )
    return {
        "attempt": {
            "attempt_id": attempt.get("attempt_id"),
            "task_id": attempt.get("task_id"),
            "team_id": attempt.get("team_id"),
            "status": attempt.get("status"),
            "submitted_at": attempt.get("submitted_at"),
            "completed_at": attempt.get("completed_at"),
        },
        "results": attempts_repo.list_validation_results(attempt_id),
    }


# ---------------------------------------------------------------------------
# Static files & SPA fallback
# ---------------------------------------------------------------------------

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        file_path = os.path.join(static_dir, full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(static_dir, "index.html"))
