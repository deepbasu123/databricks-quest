"""Read-only Databricks workspace checks for the ``databricks_sdk`` validator.

Each check is a pure-ish function ``check(client, params, ctx) -> CheckResult``
that performs **lookups only** (never creates/mutates workspace state) against a
:class:`databricks.sdk.WorkspaceClient`-shaped object and reports whether the
expected artefact exists. The ``client`` is duck-typed so unit tests can inject a
fake backend without the SDK installed.

Result contract — every check returns a dict::

    {"found": bool, "detail": "<host-facing summary>", "evidence": {...}}

Error contract:

- A missing/invalid **required param** raises :class:`SDKCheckConfigError`; the
  validator maps it to a config error (a quest-pack authoring bug, surfaced
  loudly to the host).
- Any **runtime** problem (SDK missing, API error, unknown API surface) is left
  to raise; the validator catches it and returns a ``manual`` outcome so the
  task routes to host review — a pilot is never hard-blocked by a check that
  could not run.

Registry: :data:`SDK_CHECKS` maps the check name used in quest packs to its
implementation. The names mirror those used across ``quest_packs/built_in/`` and
``samples/packs/``.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("databricks-quest.services.sdk_checks")


class SDKCheckConfigError(Exception):
    """Raised when a check's required params are missing/invalid (authoring bug)."""


CheckResult = Dict[str, Any]
SDKCheck = Callable[[Any, Dict[str, Any], Any], CheckResult]


# ── small duck-typed helpers ─────────────────────────────────────────────────

def _first_attr(obj: Any, attrs: Tuple[str, ...]) -> Optional[str]:
    for a in attrs:
        val = getattr(obj, a, None)
        if val:
            return str(val)
        if isinstance(obj, dict) and obj.get(a):
            return str(obj[a])
    return None


_NAME_ATTRS = ("display_name", "name", "label", "title")


def _iter_names(items: Optional[Iterable[Any]]) -> List[Tuple[str, Any]]:
    out: List[Tuple[str, Any]] = []
    for it in items or []:
        out.append((_first_attr(it, _NAME_ATTRS) or "", it))
    return out


def _matches(name: str, needle: Optional[str]) -> bool:
    if not needle:
        return True
    return needle.lower() in (name or "").lower()


def _require(params: Dict[str, Any], key: str) -> Any:
    val = params.get(key)
    if val is None or (isinstance(val, str) and not val.strip()):
        raise SDKCheckConfigError(f"check requires param '{key}'")
    return val


def _require_name_or_contains(params: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(name, name_contains)``; at least one must be present."""
    name = params.get("name")
    needle = params.get("name_contains")
    if not (name and str(name).strip()) and not (needle and str(needle).strip()):
        raise SDKCheckConfigError("check requires param 'name' or 'name_contains'")
    return (str(name) if name else None, str(needle) if needle else None)


def _state_token(value: Any) -> str:
    """Normalize an SDK state enum/string to a bare upper-case token.

    ``EndpointStateReady.READY`` → ``READY``; ``"online"`` → ``ONLINE``.
    """
    if value is None:
        return ""
    return str(value).rsplit(".", 1)[-1].strip().upper()


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


# ── dashboards (AI/BI Lakeview) ──────────────────────────────────────────────

def _list_dashboards(client: Any) -> List[Any]:
    lv = getattr(client, "lakeview", None)
    if lv is not None and hasattr(lv, "list"):
        return list(lv.list())
    dash = getattr(client, "dashboards", None)
    if dash is not None and hasattr(dash, "list"):
        return list(dash.list())
    raise RuntimeError("workspace client exposes no Lakeview/dashboards list API")


def dashboard_exists_for_team(client: Any, params: Dict[str, Any], ctx: Any) -> CheckResult:
    needle = params.get("name_contains")
    matches = [n for (n, _) in _iter_names(_list_dashboards(client)) if _matches(n, needle)]
    return {
        "found": bool(matches),
        "detail": f"{len(matches)} dashboard(s) match name_contains={needle!r}",
        "evidence": {"matched": matches[:5], "name_contains": needle},
    }


def dashboard_published(client: Any, params: Dict[str, Any], ctx: Any) -> CheckResult:
    needle = params.get("name_contains")
    published: List[str] = []
    any_match: List[str] = []
    for name, obj in _iter_names(_list_dashboards(client)):
        if not _matches(name, needle):
            continue
        any_match.append(name)
        state = (_first_attr(obj, ("lifecycle_state",)) or "").upper()
        is_published = bool(getattr(obj, "published", False)) or state in ("ACTIVE", "PUBLISHED")
        # When the API can't tell us publish state, fall back to existence.
        if is_published or state == "":
            published.append(name)
    return {
        "found": bool(published),
        "detail": f"{len(published)} published of {len(any_match)} matching dashboard(s)",
        "evidence": {"published": published[:5], "name_contains": needle},
    }


# ── Genie spaces ─────────────────────────────────────────────────────────────

def genie_space_exists(client: Any, params: Dict[str, Any], ctx: Any) -> CheckResult:
    needle = params.get("name_contains")
    genie = getattr(client, "genie", None)
    items: Iterable[Any]
    if genie is not None and hasattr(genie, "list_spaces"):
        resp = genie.list_spaces()
        items = getattr(resp, "spaces", None) or (resp if isinstance(resp, (list, tuple)) else [])
    else:
        raise RuntimeError("workspace client exposes no Genie list_spaces API")
    matches = [n for (n, _) in _iter_names(items) if _matches(n, needle)]
    return {
        "found": bool(matches),
        "detail": f"{len(matches)} Genie space(s) match name_contains={needle!r}",
        "evidence": {"matched": matches[:5], "name_contains": needle},
    }


# ── Unity Catalog table ──────────────────────────────────────────────────────

def table_exists(client: Any, params: Dict[str, Any], ctx: Any) -> CheckResult:
    full_name = _require(params, "table")
    tables = getattr(client, "tables", None)
    if tables is None or not hasattr(tables, "get"):
        raise RuntimeError("workspace client exposes no UC tables.get API")
    try:
        tables.get(full_name)
        found = True
        detail = f"table {full_name} exists"
    except Exception as exc:  # noqa: BLE001 - not-found is the common, expected case
        found = False
        detail = f"table {full_name} not found ({type(exc).__name__})"
    return {"found": found, "detail": detail, "evidence": {"table": full_name}}


# ── Jobs with a schedule ─────────────────────────────────────────────────────

def job_exists_with_schedule(client: Any, params: Dict[str, Any], ctx: Any) -> CheckResult:
    needle = params.get("name_contains")
    jobs_api = getattr(client, "jobs", None)
    if jobs_api is None or not hasattr(jobs_api, "list"):
        raise RuntimeError("workspace client exposes no jobs list API")
    scheduled: List[str] = []
    for job in jobs_api.list():
        settings = getattr(job, "settings", None) or job
        name = _first_attr(settings, ("name",)) or _first_attr(job, ("name",)) or ""
        if not _matches(name, needle):
            continue
        schedule = getattr(settings, "schedule", None) or getattr(settings, "trigger", None)
        if schedule:
            scheduled.append(name)
    return {
        "found": bool(scheduled),
        "detail": f"{len(scheduled)} scheduled job(s) match name_contains={needle!r}",
        "evidence": {"scheduled": scheduled[:5], "name_contains": needle},
    }


# ── Pipelines with a completed update ────────────────────────────────────────

def pipeline_update_completed(client: Any, params: Dict[str, Any], ctx: Any) -> CheckResult:
    needle = params.get("name_contains")
    pipelines = getattr(client, "pipelines", None)
    if pipelines is None:
        raise RuntimeError("workspace client exposes no pipelines API")
    lister = getattr(pipelines, "list_pipelines", None) or getattr(pipelines, "list", None)
    if lister is None:
        raise RuntimeError("workspace client exposes no pipelines list API")
    completed: List[str] = []
    matched: List[str] = []
    for p in lister():
        name = _first_attr(p, ("name",)) or ""
        if not _matches(name, needle):
            continue
        matched.append(name)
        state = (_first_attr(p, ("latest_update_state", "state")) or "").upper()
        if state in ("COMPLETED", "COMPLETE"):
            completed.append(name)
        elif state == "":
            # Update state not exposed; existence is the best signal we have.
            completed.append(name)
    return {
        "found": bool(completed),
        "detail": f"{len(completed)} of {len(matched)} matching pipeline(s) completed",
        "evidence": {"completed": completed[:5], "name_contains": needle},
    }


# ── Genie spaces: curation + conversations ──────────────────────────────────

def _resolve_genie_space_id(client: Any, params: Dict[str, Any]) -> Tuple[str, str]:
    """Return ``(space_id, title)`` from ``space_id`` or ``name_contains``."""
    genie = getattr(client, "genie", None)
    if genie is None:
        raise RuntimeError("workspace client exposes no Genie API")
    space_id = params.get("space_id")
    if space_id:
        return str(space_id), str(space_id)
    needle = params.get("name_contains")
    if not needle:
        raise SDKCheckConfigError("check requires param 'space_id' or 'name_contains'")
    if not hasattr(genie, "list_spaces"):
        raise RuntimeError("workspace client exposes no Genie list_spaces API")
    resp = genie.list_spaces()
    items = getattr(resp, "spaces", None) or (resp if isinstance(resp, (list, tuple)) else [])
    for name, obj in _iter_names(items):
        if _matches(name, needle):
            sid = _first_attr(obj, ("space_id", "id"))
            if sid:
                return sid, name
    raise _GenieSpaceNotFound(str(needle))


class _GenieSpaceNotFound(Exception):
    pass


# Key names the serialized Genie space export uses for each curation signal
# (verified against a live space export, SDK 0.94: instructions.text_instructions[],
# config.sample_questions[], data_sources.tables[]). Parsed defensively: if NONE
# of a requested signal's keys appear anywhere in the payload, the format is
# unrecognized and the check routes to host review rather than false-failing.
_GENIE_INSTRUCTION_KEYS = ("instructions", "instruction", "text_instructions", "general_instructions")
_GENIE_QUESTION_KEYS = ("curated_questions", "sample_questions", "example_questions")
_GENIE_TABLE_KEYS = ("tables", "table_identifiers")


def _collect_keys(payload: Any, keys: Tuple[str, ...]) -> List[Any]:
    """Depth-first collect values for any of ``keys`` anywhere in the payload."""
    found: List[Any] = []
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                if k in keys and v is not None:
                    found.append(v)
                stack.append(v)
        elif isinstance(node, (list, tuple)):
            stack.extend(node)
    return found


def _count_items(values: List[Any]) -> int:
    count = 0
    for v in values:
        if isinstance(v, (list, tuple)):
            count += len(v)
        elif isinstance(v, str) and v.strip():
            count += 1
    return count


def genie_space_curated(client: Any, params: Dict[str, Any], ctx: Any) -> CheckResult:
    try:
        space_id, title = _resolve_genie_space_id(client, params)
    except _GenieSpaceNotFound as exc:
        return {
            "found": False,
            "detail": f"no Genie space matches name_contains={str(exc)!r}",
            "evidence": {"name_contains": str(exc)},
        }
    genie = getattr(client, "genie")
    if not hasattr(genie, "get_space"):
        raise RuntimeError("workspace client exposes no Genie get_space API")
    try:
        space = genie.get_space(space_id, include_serialized_space=True)
    except TypeError:
        space = genie.get_space(space_id)
    serialized = getattr(space, "serialized_space", None)
    if serialized is None and isinstance(space, dict):
        serialized = space.get("serialized_space")
    if not serialized:
        raise RuntimeError(f"Genie space {space_id} export has no serialized_space payload")
    import json

    payload = json.loads(serialized) if isinstance(serialized, str) else serialized

    require_instructions = _truthy(params.get("require_instructions"))
    min_questions = int(params.get("min_sample_questions") or 0)
    min_tables = int(params.get("min_tables") or 0)

    missing: List[str] = []
    evidence: Dict[str, Any] = {"space_id": space_id, "title": title}
    if require_instructions:
        values = _collect_keys(payload, _GENIE_INSTRUCTION_KEYS)
        if not values:
            raise RuntimeError("serialized space has no recognizable instructions section")
        has_text = any(
            (isinstance(v, str) and v.strip()) or (isinstance(v, (list, dict)) and v)
            for v in values
        )
        evidence["instructions_present"] = has_text
        if not has_text:
            missing.append("instructions")
    if min_questions > 0:
        values = _collect_keys(payload, _GENIE_QUESTION_KEYS)
        if not values:
            raise RuntimeError("serialized space has no recognizable sample-questions section")
        count = _count_items(values)
        evidence["sample_questions"] = count
        if count < min_questions:
            missing.append(f"sample questions ({count}/{min_questions})")
    if min_tables > 0:
        values = _collect_keys(payload, _GENIE_TABLE_KEYS)
        if not values:
            raise RuntimeError("serialized space has no recognizable tables section")
        count = _count_items(values)
        evidence["tables"] = count
        if count < min_tables:
            missing.append(f"tables ({count}/{min_tables})")

    return {
        "found": not missing,
        "detail": (
            f"Genie space {title!r} missing: {', '.join(missing)}"
            if missing
            else f"Genie space {title!r} meets the curation bar"
        ),
        "evidence": {**evidence, "missing": missing},
    }


def genie_conversation_started(client: Any, params: Dict[str, Any], ctx: Any) -> CheckResult:
    try:
        space_id, title = _resolve_genie_space_id(client, params)
    except _GenieSpaceNotFound as exc:
        return {
            "found": False,
            "detail": f"no Genie space matches name_contains={str(exc)!r}",
            "evidence": {"name_contains": str(exc)},
        }
    genie = getattr(client, "genie")
    if not hasattr(genie, "list_conversations"):
        raise RuntimeError("workspace client exposes no Genie list_conversations API")
    try:
        resp = genie.list_conversations(space_id, include_all=True)
    except TypeError:
        resp = genie.list_conversations(space_id)
    items = getattr(resp, "conversations", None) or (resp if isinstance(resp, (list, tuple)) else [])
    conversations = list(items)

    created_after = params.get("created_after")
    if created_after:
        cutoff_ms = _to_epoch_ms(created_after)
        if cutoff_ms is not None:
            kept = []
            for conv in conversations:
                created = getattr(conv, "created_timestamp", None)
                if created is None and isinstance(conv, dict):
                    created = conv.get("created_timestamp")
                if created is None or int(created) >= cutoff_ms:
                    kept.append(conv)
            conversations = kept

    min_conversations = int(params.get("min_conversations") or 1)
    return {
        "found": len(conversations) >= min_conversations,
        "detail": (
            f"Genie space {title!r} has {len(conversations)} conversation(s) "
            f"(min {min_conversations}, created_after={created_after!r})"
        ),
        "evidence": {
            "space_id": space_id,
            "conversations": len(conversations),
            "min_conversations": min_conversations,
        },
    }


def _to_epoch_ms(value: Any) -> Optional[int]:
    """Parse an ISO timestamp / epoch value to epoch milliseconds; None if unparseable."""
    if value is None:
        return None
    try:
        return int(value) if int(value) > 10**11 else int(value) * 1000
    except (TypeError, ValueError):
        pass
    from datetime import datetime

    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


# ── Databricks Apps connected to Lakebase ────────────────────────────────────

def lakebase_app_connected(client: Any, params: Dict[str, Any], ctx: Any) -> CheckResult:
    name, needle = _require_name_or_contains(params)
    require_running = _truthy(params.get("require_running"), default=True)
    apps = getattr(client, "apps", None)
    if apps is None or not hasattr(apps, "list"):
        raise RuntimeError("workspace client exposes no Apps API")
    matched: List[str] = []
    qualified: List[str] = []
    for app_name, app in _iter_names(apps.list()):
        if name is not None and app_name != name:
            continue
        if name is None and not _matches(app_name, needle):
            continue
        matched.append(app_name)
        resources = getattr(app, "resources", None) or (
            app.get("resources") if isinstance(app, dict) else None
        ) or []
        has_database = False
        for res in resources:
            db = getattr(res, "database", None)
            if db is None and isinstance(res, dict):
                db = res.get("database")
            rtype = _state_token(_first_attr(res, ("type", "resource_type")))
            if db is not None or rtype == "DATABASE":
                has_database = True
                break
        if not has_database:
            continue
        if require_running:
            status = getattr(app, "compute_status", None) or getattr(app, "app_status", None)
            if status is None and isinstance(app, dict):
                status = app.get("compute_status") or app.get("app_status")
            token = _state_token(_first_attr(status, ("state",)) if status is not None else None)
            if token not in ("RUNNING", "ACTIVE", "AVAILABLE", ""):
                continue
        qualified.append(app_name)
    return {
        "found": bool(qualified),
        "detail": (
            f"{len(qualified)} of {len(matched)} matching app(s) have a database "
            f"resource (require_running={require_running})"
        ),
        "evidence": {"qualified": qualified[:5], "matched": matched[:5], "name": name, "name_contains": needle},
    }


# ── Agent Bricks tiles (Knowledge Assistant / Multi-Agent Supervisor) ────────
#
# Agent Bricks has no public SDK list/get as of databricks-sdk 0.94; the
# workspace exposes a beta REST surface verified live (2026-06):
#   GET /api/2.0/tiles            → {"tiles": [{tile_id, name, tile_type ∈
#                                    {KA, MAS}, serving_endpoint_name, ...}]}
#   GET /api/2.0/tiles/{tile_id}  → detail incl. instructions
# Reached through ``client.api_client.do`` so fakes stay duck-typed. If the
# surface moves, the RuntimeError routes the task to host review as usual.

_AGENT_BRICKS_TILES_PATH = "/api/2.0/tiles"


def _agent_bricks_tiles(client: Any) -> List[Dict[str, Any]]:
    api = getattr(client, "api_client", None)
    if api is None or not hasattr(api, "do"):
        raise RuntimeError("workspace client exposes no raw API client for Agent Bricks tiles")
    resp = api.do("GET", _AGENT_BRICKS_TILES_PATH)
    if not isinstance(resp, dict) or "tiles" not in resp:
        raise RuntimeError("Agent Bricks tiles API returned an unrecognized payload")
    return list(resp.get("tiles") or [])


def _agent_brick_tile_exists(
    client: Any, params: Dict[str, Any], tile_type: str, label: str
) -> CheckResult:
    name, needle = _require_name_or_contains(params)
    require_endpoint = _truthy(params.get("require_endpoint"), default=True)
    matched: List[str] = []
    qualified: List[str] = []
    for tile in _agent_bricks_tiles(client):
        if (tile.get("tile_type") or "").upper() != tile_type:
            continue
        tile_name = str(tile.get("name") or "")
        if name is not None and tile_name != name:
            continue
        if name is None and not _matches(tile_name, needle):
            continue
        matched.append(tile_name)
        if require_endpoint and not (tile.get("serving_endpoint_name") or "").strip():
            continue
        qualified.append(tile_name)
    return {
        "found": bool(qualified),
        "detail": (
            f"{len(qualified)} of {len(matched)} matching {label} tile(s) qualify "
            f"(require_endpoint={require_endpoint})"
        ),
        "evidence": {
            "qualified": qualified[:5],
            "matched": matched[:5],
            "tile_type": tile_type,
            "name": name,
            "name_contains": needle,
        },
    }


def knowledge_assistant_exists(client: Any, params: Dict[str, Any], ctx: Any) -> CheckResult:
    return _agent_brick_tile_exists(client, params, "KA", "Knowledge Assistant")


def multi_agent_supervisor_exists(client: Any, params: Dict[str, Any], ctx: Any) -> CheckResult:
    return _agent_brick_tile_exists(client, params, "MAS", "Multi-Agent Supervisor")


# ── serving endpoints + AI Gateway ───────────────────────────────────────────

def _list_serving_endpoints(client: Any) -> List[Any]:
    se = getattr(client, "serving_endpoints", None)
    if se is None or not hasattr(se, "list"):
        raise RuntimeError("workspace client exposes no serving_endpoints list API")
    return list(se.list())


def _endpoint_is_ready(endpoint: Any) -> bool:
    state = getattr(endpoint, "state", None)
    if state is None and isinstance(endpoint, dict):
        state = endpoint.get("state")
    ready = getattr(state, "ready", None)
    if ready is None and isinstance(state, dict):
        ready = state.get("ready")
    token = _state_token(ready)
    # No state exposed → existence is the best signal we have (same stance as
    # dashboard_published / pipeline_update_completed).
    return token in ("READY", "")


def serving_endpoint_exists(client: Any, params: Dict[str, Any], ctx: Any) -> CheckResult:
    name, needle = _require_name_or_contains(params)
    require_ready = _truthy(params.get("require_ready"), default=True)
    task_needle = params.get("task_contains")
    matched: List[str] = []
    qualified: List[str] = []
    for ep_name, ep in _iter_names(_list_serving_endpoints(client)):
        if name is not None and ep_name != name:
            continue
        if name is None and not _matches(ep_name, needle):
            continue
        matched.append(ep_name)
        if task_needle:
            task = _first_attr(ep, ("task",)) or ""
            if str(task_needle).lower() not in task.lower():
                continue
        if require_ready and not _endpoint_is_ready(ep):
            continue
        qualified.append(ep_name)
    return {
        "found": bool(qualified),
        "detail": (
            f"{len(qualified)} of {len(matched)} matching serving endpoint(s) qualify "
            f"(require_ready={require_ready}, task_contains={task_needle!r})"
        ),
        "evidence": {
            "qualified": qualified[:5],
            "matched": matched[:5],
            "name": name,
            "name_contains": needle,
        },
    }


_GATEWAY_FLAGS = (
    # (param flag, ai_gateway attribute, predicate description)
    ("require_rate_limits", "rate_limits", "rate limits"),
    ("require_guardrails", "guardrails", "guardrails"),
    ("require_usage_tracking", "usage_tracking_config", "usage tracking"),
    ("require_inference_table", "inference_table_config", "inference table"),
    ("require_fallbacks", "fallback_config", "fallbacks"),
)


def _gateway_feature_present(gateway: Any, attr: str) -> bool:
    val = getattr(gateway, attr, None)
    if val is None and isinstance(gateway, dict):
        val = gateway.get(attr)
    if val is None:
        return False
    if attr == "rate_limits":
        return bool(list(val))
    if attr == "guardrails":
        inp = getattr(val, "input", None) or (val.get("input") if isinstance(val, dict) else None)
        out = getattr(val, "output", None) or (val.get("output") if isinstance(val, dict) else None)
        return bool(inp or out)
    enabled = getattr(val, "enabled", None)
    if enabled is None and isinstance(val, dict):
        enabled = val.get("enabled")
    if enabled is not None:
        return bool(enabled)
    return True


def ai_gateway_configured(client: Any, params: Dict[str, Any], ctx: Any) -> CheckResult:
    name, needle = _require_name_or_contains(params)
    se = getattr(client, "serving_endpoints", None)
    if se is None or not hasattr(se, "get"):
        raise RuntimeError("workspace client exposes no serving_endpoints get API")
    if name is None:
        candidates = [n for (n, _) in _iter_names(_list_serving_endpoints(client)) if _matches(n, needle)]
        if not candidates:
            return {
                "found": False,
                "detail": f"no serving endpoint matches name_contains={needle!r}",
                "evidence": {"name_contains": needle},
            }
        name = candidates[0]
    endpoint = se.get(name)
    gateway = getattr(endpoint, "ai_gateway", None)
    if gateway is None and isinstance(endpoint, dict):
        gateway = endpoint.get("ai_gateway")
    if gateway is None:
        return {
            "found": False,
            "detail": f"endpoint {name!r} has no AI Gateway configuration",
            "evidence": {"endpoint": name, "ai_gateway": False},
        }
    required = [(flag, attr, label) for (flag, attr, label) in _GATEWAY_FLAGS if _truthy(params.get(flag))]
    missing = [label for (flag, attr, label) in required if not _gateway_feature_present(gateway, attr)]
    present = [label for (_, attr, label) in _GATEWAY_FLAGS if _gateway_feature_present(gateway, attr)]
    return {
        "found": not missing,
        "detail": (
            f"endpoint {name!r} AI Gateway missing: {', '.join(missing)}"
            if missing
            else f"endpoint {name!r} AI Gateway configured ({', '.join(present) or 'no features required'})"
        ),
        "evidence": {"endpoint": name, "features_present": present, "missing": missing},
    }


# ── Lakebase (database instances + synced tables) ────────────────────────────

def lakebase_instance_exists(client: Any, params: Dict[str, Any], ctx: Any) -> CheckResult:
    name, needle = _require_name_or_contains(params)
    require_available = _truthy(params.get("require_available"), default=True)
    db = getattr(client, "database", None)
    if db is None or not hasattr(db, "list_database_instances"):
        raise RuntimeError("workspace client exposes no database instances API")
    matched: List[str] = []
    qualified: List[str] = []
    for inst_name, inst in _iter_names(db.list_database_instances()):
        if name is not None and inst_name != name:
            continue
        if name is None and not _matches(inst_name, needle):
            continue
        matched.append(inst_name)
        token = _state_token(_first_attr(inst, ("state", "status")))
        if require_available and token not in ("AVAILABLE", ""):
            continue
        qualified.append(inst_name)
    return {
        "found": bool(qualified),
        "detail": (
            f"{len(qualified)} of {len(matched)} matching Lakebase instance(s) "
            f"qualify (require_available={require_available})"
        ),
        "evidence": {"qualified": qualified[:5], "matched": matched[:5], "name": name, "name_contains": needle},
    }


def lakebase_synced_table_online(client: Any, params: Dict[str, Any], ctx: Any) -> CheckResult:
    full_name = _require(params, "table")
    db = getattr(client, "database", None)
    if db is None or not hasattr(db, "get_synced_database_table"):
        raise RuntimeError("workspace client exposes no synced database table API")
    try:
        synced = db.get_synced_database_table(full_name)
    except Exception as exc:  # noqa: BLE001 - not-found is the common, expected case
        return {
            "found": False,
            "detail": f"synced table {full_name} not found ({type(exc).__name__})",
            "evidence": {"table": full_name},
        }
    status = getattr(synced, "data_synchronization_status", None)
    if status is None and isinstance(synced, dict):
        status = synced.get("data_synchronization_status")
    token = _state_token(_first_attr(status, ("detailed_state", "state")) if status is not None else None)
    # ONLINE family: ONLINE, ONLINE_CONTINUOUS_UPDATE, ONLINE_TRIGGERED_UPDATE, …
    # No status exposed → existence is the best signal we have.
    online = token.startswith("ONLINE") or token == ""
    return {
        "found": online,
        "detail": f"synced table {full_name} state={token or 'unknown'}",
        "evidence": {"table": full_name, "state": token},
    }


# ── Vector Search ─────────────────────────────────────────────────────────────

def vector_search_endpoint_exists(client: Any, params: Dict[str, Any], ctx: Any) -> CheckResult:
    name, needle = _require_name_or_contains(params)
    require_online = _truthy(params.get("require_online"), default=True)
    vse = getattr(client, "vector_search_endpoints", None)
    if vse is None or not hasattr(vse, "list_endpoints"):
        raise RuntimeError("workspace client exposes no vector search endpoints API")
    resp = vse.list_endpoints()
    items = getattr(resp, "endpoints", None) or (resp if isinstance(resp, (list, tuple)) else [])
    matched: List[str] = []
    qualified: List[str] = []
    for ep_name, ep in _iter_names(items):
        if name is not None and ep_name != name:
            continue
        if name is None and not _matches(ep_name, needle):
            continue
        matched.append(ep_name)
        status = getattr(ep, "endpoint_status", None)
        if status is None and isinstance(ep, dict):
            status = ep.get("endpoint_status")
        token = _state_token(_first_attr(status, ("state",)) if status is not None else None)
        if require_online and token not in ("ONLINE", ""):
            continue
        qualified.append(ep_name)
    return {
        "found": bool(qualified),
        "detail": (
            f"{len(qualified)} of {len(matched)} matching vector search endpoint(s) "
            f"qualify (require_online={require_online})"
        ),
        "evidence": {"qualified": qualified[:5], "matched": matched[:5], "name": name, "name_contains": needle},
    }


def vector_search_index_ready(client: Any, params: Dict[str, Any], ctx: Any) -> CheckResult:
    index_name = _require(params, "index")
    min_rows = int(params.get("min_rows") or 0)
    vsi = getattr(client, "vector_search_indexes", None)
    if vsi is None or not hasattr(vsi, "get_index"):
        raise RuntimeError("workspace client exposes no vector search indexes API")
    try:
        index = vsi.get_index(index_name)
    except Exception as exc:  # noqa: BLE001 - not-found is the common, expected case
        return {
            "found": False,
            "detail": f"vector search index {index_name} not found ({type(exc).__name__})",
            "evidence": {"index": index_name},
        }
    status = getattr(index, "status", None)
    if status is None and isinstance(index, dict):
        status = index.get("status")
    ready = bool(getattr(status, "ready", False) or (isinstance(status, dict) and status.get("ready")))
    rows_raw = getattr(status, "indexed_row_count", None)
    if rows_raw is None and isinstance(status, dict):
        rows_raw = status.get("indexed_row_count")
    rows = int(rows_raw or 0)
    ok = ready and rows >= min_rows
    return {
        "found": ok,
        "detail": f"index {index_name} ready={ready} rows={rows} (min_rows={min_rows})",
        "evidence": {"index": index_name, "ready": ready, "indexed_row_count": rows, "min_rows": min_rows},
    }


SDK_CHECKS: Dict[str, SDKCheck] = {
    "dashboard_exists_for_team": dashboard_exists_for_team,
    "dashboard_published": dashboard_published,
    "genie_space_exists": genie_space_exists,
    "table_exists": table_exists,
    "job_exists_with_schedule": job_exists_with_schedule,
    "pipeline_update_completed": pipeline_update_completed,
    "serving_endpoint_exists": serving_endpoint_exists,
    "ai_gateway_configured": ai_gateway_configured,
    "lakebase_instance_exists": lakebase_instance_exists,
    "lakebase_synced_table_online": lakebase_synced_table_online,
    "vector_search_endpoint_exists": vector_search_endpoint_exists,
    "vector_search_index_ready": vector_search_index_ready,
    "genie_space_curated": genie_space_curated,
    "genie_conversation_started": genie_conversation_started,
    "lakebase_app_connected": lakebase_app_connected,
    "knowledge_assistant_exists": knowledge_assistant_exists,
    "multi_agent_supervisor_exists": multi_agent_supervisor_exists,
}

# Param contracts per check, consumed by the quest-pack linter so authoring
# bugs surface at lint time instead of as runtime config errors. A string entry
# is a hard requirement; a tuple entry means "at least one of these".
REQUIRED_PARAMS: Dict[str, List[Any]] = {
    "table_exists": ["table"],
    "lakebase_synced_table_online": ["table"],
    "vector_search_index_ready": ["index"],
    "serving_endpoint_exists": [("name", "name_contains")],
    "ai_gateway_configured": [("name", "name_contains")],
    "lakebase_instance_exists": [("name", "name_contains")],
    "vector_search_endpoint_exists": [("name", "name_contains")],
    "genie_space_curated": [("space_id", "name_contains")],
    "genie_conversation_started": [("space_id", "name_contains")],
    "lakebase_app_connected": [("name", "name_contains")],
    "knowledge_assistant_exists": [("name", "name_contains")],
    "multi_agent_supervisor_exists": [("name", "name_contains")],
}

KNOWN_PARAMS: Dict[str, List[str]] = {
    "dashboard_exists_for_team": ["name_contains"],
    "dashboard_published": ["name_contains"],
    "genie_space_exists": ["name_contains"],
    "table_exists": ["table"],
    "job_exists_with_schedule": ["name_contains"],
    "pipeline_update_completed": ["name_contains"],
    "serving_endpoint_exists": ["name", "name_contains", "require_ready", "task_contains"],
    "ai_gateway_configured": [
        "name",
        "name_contains",
        "require_rate_limits",
        "require_guardrails",
        "require_usage_tracking",
        "require_inference_table",
        "require_fallbacks",
    ],
    "lakebase_instance_exists": ["name", "name_contains", "require_available"],
    "lakebase_synced_table_online": ["table"],
    "vector_search_endpoint_exists": ["name", "name_contains", "require_online"],
    "vector_search_index_ready": ["index", "min_rows"],
    "genie_space_curated": [
        "space_id",
        "name_contains",
        "require_instructions",
        "min_sample_questions",
        "min_tables",
    ],
    "genie_conversation_started": [
        "space_id",
        "name_contains",
        "min_conversations",
        "created_after",
    ],
    "lakebase_app_connected": ["name", "name_contains", "require_running"],
    "knowledge_assistant_exists": ["name", "name_contains", "require_endpoint"],
    "multi_agent_supervisor_exists": ["name", "name_contains", "require_endpoint"],
}


def known_checks() -> List[str]:
    return sorted(SDK_CHECKS.keys())
