"""Resource bootstrap & reset orchestration (PR08).

Turns an event + its teams + the pack's optional ``resources`` section into a
deterministic, inspectable **plan** of SQL statements, then executes that plan
against a Databricks warehouse via an injected executor.

Safety is layered:
- :mod:`services.namespace` computes every target and is the sole authority on
  what is in-namespace; this service never invents a target name.
- A plan is built first and can be returned for **dry-run** without executing.
- Execution refuses to run if any statement is flagged out-of-namespace, and
  reset re-validates every DROP target against the namespace immediately before
  emitting it.

The executor signature matches ``services.sql_runner.Executor``
(``Callable[[str, int], List[dict]]``) so the warehouse runner can be reused;
tests inject a fake.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional

from services import namespace as ns

logger = logging.getLogger("databricks-quest.services.resource_service")

Executor = Callable[[str, int], List[Dict[str, Any]]]


def _seed_statements(resources: Optional[Dict[str, Any]]) -> List[str]:
    """Extract pack seed SQL statements (``resources.seed_sql``: list[str])."""
    if not isinstance(resources, dict):
        return []
    raw = resources.get("seed_sql") or []
    if isinstance(raw, str):
        raw = [raw]
    return [s for s in raw if isinstance(s, str) and s.strip()]


class ResourcePlanItem(dict):
    """A single planned statement. A plain dict subclass for easy JSON return."""


def build_bootstrap_plan(
    event: Dict[str, Any],
    teams: List[Dict[str, Any]],
    resources: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Return the ordered list of statements to provision all team resources.

    For each team: ensure the catalog, ensure the team schema, then any pack
    seed statements (rendered against the team's namespace). Every item carries
    ``within_namespace`` so a dry-run can surface blockers without executing.
    """
    items: List[Dict[str, Any]] = []
    seen_catalogs: set[str] = set()
    targets = ns.team_targets(event, teams)
    event_id = event.get("event_id")
    seeds = _seed_statements(resources)

    for target in targets:
        catalog, schema, fqn = target["catalog"], target["schema"], target["fqn"]
        if catalog not in seen_catalogs:
            seen_catalogs.add(catalog)
            items.append(ResourcePlanItem(
                op="create_catalog", team_id=target["team_id"],
                resource_type="catalog", target=catalog,
                sql=f"CREATE CATALOG IF NOT EXISTS {catalog}",
                within_namespace=True,
            ))
        items.append(ResourcePlanItem(
            op="create_schema", team_id=target["team_id"],
            resource_type="schema", target=fqn,
            sql=f"CREATE SCHEMA IF NOT EXISTS {fqn}",
            within_namespace=True,
        ))
        for raw_sql in seeds:
            rendered = ns.render_seed_sql(raw_sql, target, event_id)
            items.append(ResourcePlanItem(
                op="seed", team_id=target["team_id"],
                resource_type="seed", target=fqn,
                sql=rendered,
                # Seed SQL is host-authored pack content scoped to the team
                # schema via templating; it is considered in-namespace.
                within_namespace=True,
            ))
    return items


def build_reset_plan(
    event: Dict[str, Any], teams: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Return the ordered list of DROP statements for an event's team schemas.

    Each target is re-validated against the namespace; a target that fails the
    guard is included with ``within_namespace=False`` and an ``error`` so a
    dry-run shows it and execution refuses the whole plan.
    """
    items: List[Dict[str, Any]] = []
    for target in ns.team_targets(event, teams):
        fqn = target["fqn"]
        item = ResourcePlanItem(
            op="drop_schema", team_id=target["team_id"],
            resource_type="schema", target=fqn,
            sql=f"DROP SCHEMA IF EXISTS {fqn} CASCADE",
        )
        try:
            ns.assert_within_namespace(fqn, event, teams)
            item["within_namespace"] = True
        except ns.NamespaceError as exc:
            item["within_namespace"] = False
            item["error"] = str(exc)
        items.append(item)
    return items


def plan_blockers(plan: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return plan items that are out-of-namespace (execution must refuse these)."""
    return [i for i in plan if not i.get("within_namespace", False)]


# ── Workspace resources (PR21) ───────────────────────────────────────────────
#
# ``resources.workspace`` entries in a pack request non-SQL resources the host
# bootstrap should provision: Lakebase instances, vector search endpoints,
# serving endpoints, Genie spaces. Templates resolve event/team slots; rendered
# names must pass the namespace prefix guard. Caps are a cost guardrail.

KNOWN_WORKSPACE_RESOURCE_TYPES = {
    "lakebase_instance",
    "vector_search_endpoint",
    "serving_endpoint",
    "genie_space",
}

WORKSPACE_CAPS = {
    "lakebase_instance": 1,
    "vector_search_endpoint": 1,
    "serving_endpoint": 32,
    "genie_space": 64,
}

_WS_SLOT_RE = re.compile(r"\$\{([a-zA-Z_]+)\}")


def _render_workspace_value(value: Any, variables: Dict[str, str]) -> Any:
    """Resolve ``${...}`` slots in strings (recursively for dicts/lists).

    Unknown slots raise — a workspace resource may never carry an unresolved
    (and therefore unguarded) name fragment."""
    if isinstance(value, str):
        def _sub(m):
            slot = m.group(1)
            if slot not in variables:
                raise ns.NamespaceError(
                    f"Unknown template slot ${{{slot}}} in workspace resource.",
                    code="BAD_RESOURCE_SLOT",
                )
            return variables[slot]
        return _WS_SLOT_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _render_workspace_value(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [_render_workspace_value(v, variables) for v in value]
    return value


def _workspace_entries(resources: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(resources, dict):
        return []
    raw = resources.get("workspace") or []
    return [e for e in raw if isinstance(e, dict)]


def build_workspace_plan(
    event: Dict[str, Any],
    teams: List[Dict[str, Any]],
    resources: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Plan the workspace resources a pack requests.

    Entries whose ``name`` template references ``${team_slug}``/``${team_*}``
    render once per team; others render once for the event. Every rendered name
    passes :func:`ns.assert_resource_name_in_namespace` or the item becomes a
    blocker. Type caps produce blockers too — never silent truncation.
    """
    items: List[Dict[str, Any]] = []
    entries = _workspace_entries(resources)
    if not entries:
        return items

    event_slug = str(event.get("slug") or event.get("event_id") or "event")
    targets = ns.team_targets(event, teams)
    event_ns = ns.event_namespace(event)
    type_counts: Dict[str, int] = {}

    for entry in entries:
        rtype = str(entry.get("type") or "").strip()
        name_template = str(entry.get("name") or "").strip()
        spec = entry.get("spec") if isinstance(entry.get("spec"), dict) else {}
        per_team = "${team_" in name_template

        scopes: List[Dict[str, str]]
        if per_team:
            scopes = [
                {
                    "event_slug": event_slug,
                    "team_slug": ns.sanitize_identifier(t.get("team_name") or str(t.get("team_id"))).replace("_", "-"),
                    "team_catalog": t["catalog"],
                    "team_schema": t["schema"],
                    "event_catalog": event_ns["catalog"],
                    "team_id": t.get("team_id"),
                }
                for t in targets
            ]
        else:
            scopes = [{
                "event_slug": event_slug,
                "event_catalog": event_ns["catalog"],
                "team_id": None,
            }]

        for scope in scopes:
            team_id = scope.pop("team_id", None)
            item = ResourcePlanItem(
                op=f"create_{rtype}", kind="workspace",
                resource_type=rtype, team_id=team_id,
            )
            try:
                if rtype not in KNOWN_WORKSPACE_RESOURCE_TYPES:
                    raise ns.NamespaceError(
                        f"Unknown workspace resource type {rtype!r}.",
                        code="UNKNOWN_RESOURCE_TYPE",
                    )
                name = _render_workspace_value(name_template, scope)
                ns.assert_resource_name_in_namespace(name, event)
                type_counts[rtype] = type_counts.get(rtype, 0) + 1
                cap = WORKSPACE_CAPS.get(rtype)
                if cap is not None and type_counts[rtype] > cap:
                    raise ns.NamespaceError(
                        f"Plan requests more than {cap} {rtype} resource(s) — "
                        "split the event or raise the cap deliberately.",
                        code="RESOURCE_CAP_EXCEEDED",
                    )
                item["target"] = name
                item["spec"] = _render_workspace_value(spec, scope)
                item["within_namespace"] = True
            except ns.NamespaceError as exc:
                item["target"] = name_template
                item["within_namespace"] = False
                item["error"] = str(exc)
            items.append(item)
    return items


def build_workspace_teardown_plan(
    event: Dict[str, Any], registry_rows: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Plan deletion of the event's registry-recorded workspace resources.

    Deletion is doubly gated: the resource must be **recorded in the event's
    registry** (we never enumerate the workspace for things to delete) and its
    name must still pass the namespace prefix guard."""
    items: List[Dict[str, Any]] = []
    for row in registry_rows:
        rtype = row.get("resource_type")
        if rtype not in KNOWN_WORKSPACE_RESOURCE_TYPES:
            continue
        if row.get("status") == "removed":
            continue
        name = row.get("fqn") or ""
        item = ResourcePlanItem(
            op=f"delete_{rtype}", kind="workspace",
            resource_type=rtype, target=name, team_id=row.get("team_id"),
        )
        try:
            ns.assert_resource_name_in_namespace(name, event)
            item["within_namespace"] = True
        except ns.NamespaceError as exc:
            item["within_namespace"] = False
            item["error"] = str(exc)
        items.append(item)
    return items


def build_catalog_teardown_plan(
    event: Dict[str, Any], teams: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """The final teardown step: drop the event's own catalog.

    Only the namespace-computed catalog, never a host-typed name; the reserved
    list is enforced inside :func:`ns.event_namespace`."""
    catalog = ns.event_namespace(event)["catalog"]
    return [ResourcePlanItem(
        op="drop_catalog", kind="sql", resource_type="catalog", target=catalog,
        sql=f"DROP CATALOG IF EXISTS {catalog} CASCADE",
        within_namespace=True,
    )]


class SDKWorkspaceExecutor:
    """Create/delete workspace resources via the Databricks SDK.

    Duck-typed and injectable (tests pass a fake with the same ``execute``
    signature). Each op either succeeds or raises; the service records the
    outcome per item. Lakebase/VS creates are fire-and-forget (no ``_and_wait``)
    so a bootstrap over many teams isn't serialized on provisioning time —
    readiness is what the SDK *checks* verify during play.
    """

    def __init__(self, client_factory: Optional[Callable[[], Any]] = None):
        self._client_factory = client_factory
        self._client_cache: Any = None

    def _client(self) -> Any:
        if self._client_cache is None:
            if self._client_factory is not None:
                self._client_cache = self._client_factory()
            else:
                from databricks.sdk import WorkspaceClient

                self._client_cache = WorkspaceClient()
        return self._client_cache

    def execute(self, item: Dict[str, Any]) -> None:
        op, name = item.get("op", ""), item.get("target", "")
        spec = item.get("spec") or {}
        w = self._client()
        if op == "create_lakebase_instance":
            from databricks.sdk.service.database import DatabaseInstance

            w.database.create_database_instance(
                DatabaseInstance(name=name, capacity=str(spec.get("capacity") or "CU_1"))
            )
        elif op == "delete_lakebase_instance":
            w.database.delete_database_instance(name)
        elif op == "create_vector_search_endpoint":
            from databricks.sdk.service.vectorsearch import EndpointType

            w.vector_search_endpoints.create_endpoint(
                name=name,
                endpoint_type=EndpointType(str(spec.get("endpoint_type") or "STANDARD")),
            )
        elif op == "delete_vector_search_endpoint":
            w.vector_search_endpoints.delete_endpoint(name)
        elif op == "create_serving_endpoint":
            # Raw REST keeps the spec passthrough simple (the SDK dataclasses
            # for serving configs are deep); spec is the endpoint's `config`.
            w.api_client.do(
                "POST", "/api/2.0/serving-endpoints",
                body={"name": name, "config": spec.get("config") or spec},
            )
        elif op == "delete_serving_endpoint":
            w.serving_endpoints.delete(name)
        elif op == "create_genie_space":
            # Best-effort: the serialized create payload is workspace-version
            # dependent. Failure is recorded per-item, never fatal to the plan.
            import json

            serialized = json.dumps({
                "version": 1,
                "data_sources": {
                    "tables": [{"identifier": t} for t in (spec.get("tables") or [])]
                },
            })
            w.genie.create_space(
                serialized_space=serialized,
                title=name,
                warehouse_id=spec.get("warehouse_id") or os.getenv("QUEST_SQL_WAREHOUSE_ID") or None,
            )
        elif op == "delete_genie_space":
            # Resolve by exact title within the namespace; trash, don't purge.
            genie = w.genie
            resp = genie.list_spaces()
            for space in getattr(resp, "spaces", None) or []:
                if (getattr(space, "title", "") or "") == name:
                    genie.trash_space(space.space_id)
                    return
            raise RuntimeError(f"Genie space titled {name!r} not found")
        else:
            raise RuntimeError(f"Unknown workspace op {op!r}")


class ResourceService:
    def __init__(self, repo=None):
        # Lazy import keeps namespace/plan tests free of any DB dependency.
        if repo is None:
            from repositories.resources import ResourcesRepository

            repo = ResourcesRepository()
        self._repo = repo

    def execute_plan(
        self,
        event: Dict[str, Any],
        plan: List[Dict[str, Any]],
        executor: Executor,
        *,
        created_by: Optional[str] = None,
        timeout_seconds: int = 60,
        record: bool = True,
    ) -> Dict[str, Any]:
        """Run a plan's statements in order, recording resource health.

        Refuses to run anything if the plan contains an out-of-namespace item.
        Returns ``{"executed": [...], "ok": bool, "blockers": [...]}`` where each
        executed item gets ``status`` (``ok``/``error``) and any ``error``.
        """
        blockers = plan_blockers(plan)
        if blockers:
            return {"ok": False, "executed": [], "blockers": blockers}

        event_id = event.get("event_id")
        executed: List[Dict[str, Any]] = []
        all_ok = True
        for item in plan:
            entry = dict(item)
            try:
                executor(item["sql"], timeout_seconds)
                entry["status"] = "ok"
            except Exception as exc:  # noqa: BLE001 - capture per-statement failure
                all_ok = False
                entry["status"] = "error"
                entry["error"] = str(exc)
                logger.warning("resource statement failed (%s): %s", item.get("op"), exc)

            if record and item.get("resource_type") in ("catalog", "schema"):
                removed = item.get("op") == "drop_schema"
                status = (
                    "removed" if (removed and entry["status"] == "ok")
                    else ("active" if entry["status"] == "ok" else "failed")
                )
                self._repo.upsert(
                    event_id=event_id,
                    fqn=item["target"],
                    resource_type=item["resource_type"],
                    status=status,
                    team_id=item.get("team_id"),
                    message=entry.get("error"),
                    created_by=created_by,
                )
            executed.append(entry)
        return {"ok": all_ok, "executed": executed, "blockers": []}

    def execute_workspace_plan(
        self,
        event: Dict[str, Any],
        plan: List[Dict[str, Any]],
        workspace_executor: Any,
        *,
        created_by: Optional[str] = None,
        record: bool = True,
    ) -> Dict[str, Any]:
        """Run workspace-resource plan items in order, recording each outcome.

        Same refusal semantics as :meth:`execute_plan`: any blocker stops the
        whole plan before anything executes. Per-item failures are recorded
        (status ``failed``) and don't abort later items — a half-provisioned
        event is visible in the registry, not hidden."""
        blockers = plan_blockers(plan)
        if blockers:
            return {"ok": False, "executed": [], "blockers": blockers}

        event_id = event.get("event_id")
        executed: List[Dict[str, Any]] = []
        all_ok = True
        for item in plan:
            entry = dict(item)
            entry.pop("spec", None)  # specs can be bulky; keep responses lean
            try:
                workspace_executor.execute(item)
                entry["status"] = "ok"
            except Exception as exc:  # noqa: BLE001 - capture per-item failure
                all_ok = False
                entry["status"] = "error"
                entry["error"] = str(exc)
                logger.warning("workspace resource op failed (%s): %s", item.get("op"), exc)

            if record:
                removed = str(item.get("op", "")).startswith("delete_")
                status = (
                    "removed" if (removed and entry["status"] == "ok")
                    else ("active" if entry["status"] == "ok" else "failed")
                )
                self._repo.upsert(
                    event_id=event_id,
                    fqn=item["target"],
                    resource_type=item["resource_type"],
                    status=status,
                    team_id=item.get("team_id"),
                    message=entry.get("error"),
                    created_by=created_by,
                )
            executed.append(entry)
        return {"ok": all_ok, "executed": executed, "blockers": []}


default_resource_service = ResourceService()
