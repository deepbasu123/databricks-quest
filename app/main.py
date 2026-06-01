import os
import time
import logging
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional
from decimal import Decimal

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from databricks.sdk import WorkspaceClient
import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("databricks-quest")

app = FastAPI(title="Databricks Quest API")

LAKEBASE_HOST = os.getenv("LAKEBASE_HOST", "")
LAKEBASE_DB = os.getenv("LAKEBASE_DB", "quest_db")

MISSION_DEFINITIONS = [
    {
        "id": "first_steps",
        "name": "First Steps",
        "description": "Record your first Databricks compute usage",
        "points": 25,
        "category": "Getting Started",
        "award_type": "one_time",
        "icon": "rocket",
    },
    {
        "id": "job_creator",
        "name": "Job Creator",
        "description": "Create your first Lakeflow Job",
        "points": 100,
        "category": "Data Engineering",
        "award_type": "one_time",
        "icon": "briefcase",
    },
    {
        "id": "pipeline_builder",
        "name": "Pipeline Builder",
        "description": "Create your first Lakeflow Spark Declarative Pipeline",
        "points": 150,
        "category": "Data Engineering",
        "award_type": "one_time",
        "icon": "git-branch",
    },
    {
        "id": "pipeline_runner",
        "name": "Pipeline Runner",
        "description": "Complete your first successful pipeline update",
        "points": 200,
        "category": "Data Engineering",
        "award_type": "one_time",
        "icon": "play-circle",
    },
    {
        "id": "scheduler",
        "name": "Scheduler",
        "description": "Create a scheduled or CRON-triggered job",
        "points": 150,
        "category": "Data Engineering",
        "award_type": "one_time",
        "icon": "clock",
    },
    {
        "id": "auto_loader_pioneer",
        "name": "Auto Loader Pioneer",
        "description": "Use Auto Loader in a pipeline for streaming ingestion",
        "points": 250,
        "category": "Data Engineering",
        "award_type": "one_time",
        "icon": "upload-cloud",
    },
    {
        "id": "genie_creator",
        "name": "Genie Creator",
        "description": "Create your first AI/BI Genie space",
        "points": 200,
        "category": "Analytics",
        "award_type": "one_time",
        "icon": "sparkles",
    },
    {
        "id": "dashboard_designer",
        "name": "Dashboard Designer",
        "description": "Create your first Databricks Dashboard",
        "points": 150,
        "category": "Analytics",
        "award_type": "one_time",
        "icon": "layout-dashboard",
    },
    {
        "id": "consistent_operator",
        "name": "Consistent Operator",
        "description": "Run pipelines or jobs on 7 distinct days within 30 days",
        "points": 300,
        "category": "Engagement",
        "award_type": "repeatable",
        "icon": "calendar-check",
    },
    {
        "id": "data_explorer",
        "name": "Data Explorer",
        "description": "Execute 50+ SQL queries in a single week",
        "points": 150,
        "category": "Analytics",
        "award_type": "repeatable",
        "icon": "search",
    },
]

BADGE_DEFINITIONS = [
    {
        "id": "pipeline_craftsman",
        "name": "Pipeline Craftsman",
        "description": "Complete 5 pipeline-related missions",
        "icon": "wrench",
        "required_missions": 5,
        "mission_filter": ["pipeline_builder", "pipeline_runner", "auto_loader_pioneer", "consistent_operator", "scheduler"],
    },
    {
        "id": "platform_explorer",
        "name": "Platform Explorer",
        "description": "Use 4+ distinct Databricks product areas",
        "icon": "compass",
        "required_products": 4,
    },
    {
        "id": "consistent_contributor",
        "name": "Consistent Contributor",
        "description": "Maintain a 14-day activity streak",
        "icon": "flame",
        "required_streak": 14,
    },
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


def serialize(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    return obj


def get_workspace_client() -> WorkspaceClient:
    return WorkspaceClient()


# --- Lakebase connection with token caching ---
_conn_cache = {"conn": None, "expiry": 0}


def get_lakebase_connection():
    now = time.time()
    cached = _conn_cache["conn"]
    if cached and _conn_cache["expiry"] > now:
        try:
            with cached.cursor() as c:
                c.execute("SELECT 1")
            return cached
        except Exception:
            try:
                cached.close()
            except Exception:
                pass

    w = get_workspace_client()
    headers = w.config.authenticate()
    auth_header = headers.get("Authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else w.config.token
    user = w.current_user.me().user_name

    conn = psycopg2.connect(
        host=LAKEBASE_HOST,
        port=5432,
        dbname=LAKEBASE_DB,
        user=user,
        password=token,
        sslmode="require",
        connect_timeout=10,
    )
    conn.autocommit = True
    _conn_cache["conn"] = conn
    _conn_cache["expiry"] = now + 2700  # 45 min
    logger.info(f"Lakebase connection established as {user}")
    return conn


def execute_query(query: str, params: tuple = ()) -> List[Dict[str, Any]]:
    conn = get_lakebase_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params)
        if cur.description:
            rows = cur.fetchall()
            return [{k: serialize(v) for k, v in dict(row).items()} for row in rows]
        return []


def get_user_email(request: Request) -> str:
    email = request.headers.get("X-Forwarded-Email", "")
    if not email:
        email = request.headers.get("X-Forwarded-User", "")
    if not email:
        email = os.getenv("QUEST_DEFAULT_USER", "demo@databricks.com")
    return email


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
    return {"status": "ok" if db_ok else "degraded", "db_connected": db_ok, "timestamp": datetime.utcnow().isoformat()}


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
