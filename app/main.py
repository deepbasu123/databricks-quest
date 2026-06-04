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
    LeaderboardRepository,
    FederationRepository,
    RosterImportError,
)
from services import quest_pack_loader
from services import federation as fed
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


def require_host(request: Request) -> str:
    """FastAPI dependency: resolve the user and enforce the host allowlist."""
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
leaderboard_repo = LeaderboardRepository()
federation_repo = FederationRepository()


@app.on_event("startup")
async def _federation_startup() -> None:
    """Child role: record this workspace's presence in the shared DB once."""
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
async def federation_status(request: Request):
    """Role + this workspace's own team mapping (child 'your rank' context).

    Includes a DB-connection health flag the child UI uses for its indicator.
    """
    user = get_user_email(request)
    status = fed.child_status(submitted_by=user)
    status["submitted_by"] = user
    try:
        status["db_connected"] = db.healthcheck()
    except Exception:  # noqa: BLE001
        status["db_connected"] = False
    return status


@app.get("/api/federation/leaderboard")
async def federation_leaderboard(request: Request, event: Optional[str] = None):
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
