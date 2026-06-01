import os
import logging
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional
from decimal import Decimal

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from databricks.sdk import WorkspaceClient
from databricks import sql as dbsql

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("databricks-quest")

app = FastAPI(title="Databricks Quest API")

WAREHOUSE_ID = os.getenv("DATABRICKS_WAREHOUSE_ID", "")
QUEST_CATALOG = os.getenv("QUEST_CATALOG", "deep_test_1_catalog")
QUEST_SCHEMA = os.getenv("QUEST_SCHEMA", "quest")

MISSION_DEFINITIONS = [
    {
        "id": "first_steps",
        "name": "First Steps",
        "description": "Record your first billable Databricks usage",
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


def row_to_dict(columns, row):
    return {col: serialize(val) for col, val in zip(columns, row)}


def get_workspace_client() -> WorkspaceClient:
    return WorkspaceClient()


def get_sql_connection():
    w = get_workspace_client()
    hostname = w.config.host or ""
    hostname = hostname.replace("https://", "").replace("http://", "").rstrip("/")
    return dbsql.connect(
        server_hostname=hostname,
        http_path=f"/sql/1.0/warehouses/{WAREHOUSE_ID}",
        access_token=w.config.token,
    )


def execute_query(query: str) -> List[Dict[str, Any]]:
    conn = get_sql_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query)
        if cursor.description:
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            return [row_to_dict(columns, row) for row in rows]
        return []
    finally:
        conn.close()


def tbl(name: str) -> str:
    return f"`{QUEST_CATALOG}`.`{QUEST_SCHEMA}`.`{name}`"


def get_user_email(request: Request) -> str:
    email = request.headers.get("X-Forwarded-Email", "")
    if not email:
        email = request.headers.get("X-Forwarded-User", "")
    if not email:
        email = os.getenv("QUEST_DEFAULT_USER", "demo@databricks.com")
    return email


def safe_sql_string(s: str) -> str:
    return s.replace("'", "''").replace("\\", "\\\\")


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
    user = safe_sql_string(get_user_email(request))
    try:
        profiles = execute_query(f"SELECT * FROM {tbl('user_profile_snapshot')} WHERE user_id = '{user}'")
        if profiles:
            profile = profiles[0]
            profile["level_progress"] = get_level_progress(int(profile.get("total_points", 0)))
            badges = execute_query(f"SELECT * FROM {tbl('badges')} WHERE user_id = '{user}' ORDER BY earned_at DESC")
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
    user = safe_sql_string(get_user_email(request))
    missions = [m.copy() for m in MISSION_DEFINITIONS]
    try:
        completions = execute_query(
            f"SELECT mission_id, completed_at, points_awarded FROM {tbl('mission_completions')} WHERE user_id = '{user}'"
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
            col = "weekly_points"
            rank_col = "weekly_rank"
        elif period == "monthly":
            col = "monthly_points"
            rank_col = "monthly_rank"
        else:
            col = "total_points"
            rank_col = "all_time_rank"

        rows = execute_query(
            f"""
            SELECT user_id, display_name, total_points, weekly_points, monthly_points,
                   level, all_time_rank, weekly_rank, monthly_rank
            FROM {tbl('leaderboard')}
            ORDER BY {rank_col} ASC
            LIMIT 100
            """
        )
        return {"leaderboard": rows, "period": period}
    except Exception as e:
        logger.warning(f"Leaderboard error: {e}")
        return {"leaderboard": [], "period": period}


@app.get("/api/notifications")
async def get_notifications(request: Request):
    user = safe_sql_string(get_user_email(request))
    try:
        rows = execute_query(
            f"""
            SELECT notification_type, title, message, mission_id, points, created_at
            FROM {tbl('notifications')}
            WHERE user_id = '{user}'
            ORDER BY created_at DESC
            LIMIT 20
            """
        )
        return {"notifications": rows}
    except Exception:
        return {"notifications": []}


@app.get("/api/admin/stats")
async def get_admin_stats():
    try:
        user_count = execute_query(f"SELECT COUNT(DISTINCT user_id) AS cnt FROM {tbl('user_profile_snapshot')}")
        mission_count = execute_query(f"SELECT COUNT(*) AS cnt FROM {tbl('mission_completions')}")
        top_missions = execute_query(
            f"""
            SELECT mission_id, mission_name, COUNT(*) AS completions
            FROM {tbl('mission_completions')}
            GROUP BY mission_id, mission_name
            ORDER BY completions DESC
            """
        )
        level_dist = execute_query(
            f"""
            SELECT level, COUNT(*) AS cnt
            FROM {tbl('user_profile_snapshot')}
            GROUP BY level
            ORDER BY cnt DESC
            """
        )
        latest_run = execute_query(
            f"SELECT MAX(updated_at) AS last_refresh FROM {tbl('user_profile_snapshot')}"
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
        latest = execute_query(f"SELECT MAX(updated_at) AS last_run FROM {tbl('user_profile_snapshot')}")
        last_run = latest[0]["last_run"] if latest and latest[0].get("last_run") else None
        total = execute_query(f"SELECT COUNT(*) AS cnt FROM {tbl('user_points_fact')}")
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
