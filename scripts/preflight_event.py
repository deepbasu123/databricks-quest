#!/usr/bin/env python3
"""Operator preflight: prove a quest pack is playable end-to-end, for real.

Runs the FULL event lifecycle against a deployed GameDay app and a real
workspace, playing every task the way a team would:

    lint+import pack → create event + preflight team → join → start →
    bootstrap (dry-run, then execute) → per task: execute its host-only
    ``solutions`` steps (SQL via the warehouse, workspace ops via the SDK) →
    submit the attempt over HTTP → assert the validation outcome matches what
    the pack declares → leaderboard sanity → reset + teardown (unless --keep).

Exit code 0 means: a team that does the work will score on every task of this
pack, on this workspace, today. This is the documented step 0 of every event
(docs/19) and the real-workspace tier of the test strategy.

Usage:
    python scripts/preflight_event.py \
        --app-url https://<app-host> --pack quest_packs/built_in/<pack>.yml \
        --warehouse-id <id> [--token ...] [--keep] [--skip-teardown]

Auth: ``--token`` / ``QUEST_APP_TOKEN`` for the app (the caller must pass the
host gate); standard SDK resolution for the workspace side. The preflight
plays as the caller's identity on a team named ``preflight``.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "app"))

import yaml  # noqa: E402

_SLOT_RE = re.compile(r"\$\{([a-z_]+)\}")


# ── app HTTP client ───────────────────────────────────────────────────────────


class AppClient:
    def __init__(self, base_url: str, token: str):
        self.base = base_url.rstrip("/")
        self.token = token

    def call(self, method: str, path: str, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            raise RuntimeError(f"HTTP {exc.code} {method} {path}: {body[:400]}")


# ── workspace-op runner (executes solutions' workspace_op steps) ──────────────


class SolutionOps:
    """Execute ``workspace_op`` solution steps with the operator's identity.

    Every op either succeeds or raises; the harness reports per-op outcomes.
    Ops mirror the vocabulary documented in AUTHORING_QUEST_PACKS.md.
    """

    def __init__(self, client, warehouse_id: str):
        self.w = client
        self.warehouse_id = warehouse_id
        self._workspace_dir = None

    # — helpers —
    def _genie_space_id(self, title: str) -> str:
        resp = self.w.genie.list_spaces()
        for space in getattr(resp, "spaces", None) or []:
            if (getattr(space, "title", "") or "") == title:
                return space.space_id
        raise RuntimeError(f"Genie space titled {title!r} not found")

    def _pipeline_id(self, name: str):
        lister = getattr(self.w.pipelines, "list_pipelines", None) or self.w.pipelines.list
        for p in lister():
            if (getattr(p, "name", "") or "") == name:
                return p.pipeline_id
        return None

    def _ensure_workspace_dir(self) -> str:
        if self._workspace_dir is None:
            me = self.w.current_user.me().user_name
            self._workspace_dir = f"/Workspace/Users/{me}/quest_preflight"
            self.w.workspace.mkdirs(self._workspace_dir)
        return self._workspace_dir

    # — ops —
    def publish_dashboard(self, op):
        from databricks.sdk.service.dashboards import Dashboard

        name = op["name"]
        dataset_sql = op.get("dataset_sql") or "SELECT 1"
        serialized = json.dumps({
            "datasets": [{"name": "main", "displayName": "main", "query": dataset_sql}],
            "pages": [{
                "name": "page1", "displayName": "Overview",
                "layout": [{
                    "widget": {
                        "name": "w1",
                        "queries": [{"name": "q1", "query": {"datasetName": "main",
                                     "fields": [{"name": "*", "expression": "*"}],
                                     "disaggregated": True}}],
                        "spec": {"version": 1, "widgetType": "table"},
                    },
                    "position": {"x": 0, "y": 0, "width": 6, "height": 6},
                }],
            }],
        })
        dash = self.w.lakeview.create(
            dashboard=Dashboard(
                display_name=name,
                serialized_dashboard=serialized,
                warehouse_id=self.warehouse_id,
            )
        )
        self.w.lakeview.publish(dash.dashboard_id, warehouse_id=self.warehouse_id)

    def create_genie_space(self, op):
        serialized = json.dumps({
            "version": 1,
            "data_sources": {"tables": [{"identifier": t} for t in (op.get("tables") or [])]},
        })
        self.w.genie.create_space(
            serialized_space=serialized, title=op["name"], warehouse_id=self.warehouse_id
        )

    def curate_genie_space(self, op):
        space_id = self._genie_space_id(op["name"])
        space = self.w.genie.get_space(space_id, include_serialized_space=True)
        payload = json.loads(space.serialized_space or "{}")
        if op.get("instructions"):
            payload.setdefault("instructions", {})["text_instructions"] = [
                {"id": "preflight-1", "content": str(op["instructions"]).strip()}
            ]
        if op.get("sample_questions"):
            payload.setdefault("config", {})["sample_questions"] = [
                {"id": f"preflight-q{i}", "question": q}
                for i, q in enumerate(op["sample_questions"], start=1)
            ]
        self.w.genie.update_space(space_id, serialized_space=json.dumps(payload))

    def start_genie_conversation(self, op):
        space_id = self._genie_space_id(op["space_name"])
        waiter = getattr(self.w.genie, "start_conversation_and_wait", None)
        if waiter is not None:
            waiter(space_id, op["question"])
        else:
            self.w.genie.start_conversation(space_id, op["question"])

    def create_pipeline_and_run(self, op):
        from databricks.sdk.service.pipelines import (
            FileLibrary,
            PipelineLibrary,
        )

        import base64

        from databricks.sdk.service.workspace import ImportFormat

        name = op["name"]
        path = f"{self._ensure_workspace_dir()}/{name}.sql"
        self.w.workspace.import_(
            path=path,
            content=base64.b64encode(str(op["pipeline_sql"]).encode()).decode(),
            format=ImportFormat.AUTO,
            overwrite=True,
        )
        pipeline_id = self._pipeline_id(name)
        if pipeline_id is None:
            created = self.w.pipelines.create(
                name=name,
                serverless=True,
                catalog=op.get("target_catalog"),
                schema=op.get("target_schema"),
                libraries=[PipelineLibrary(file=FileLibrary(path=path))],
            )
            pipeline_id = created.pipeline_id
        update = self.w.pipelines.start_update(pipeline_id)
        deadline = time.time() + 900
        while time.time() < deadline:
            info = self.w.pipelines.get_update(pipeline_id, update.update_id)
            state = str(getattr(info.update, "state", "")).rsplit(".", 1)[-1].upper()
            if state == "COMPLETED":
                return
            if state in ("FAILED", "CANCELED"):
                raise RuntimeError(f"pipeline update ended {state}")
            time.sleep(15)
        raise RuntimeError("pipeline update timed out after 15 minutes")

    def create_scheduled_job(self, op):
        from databricks.sdk.service.jobs import CronSchedule, PipelineTask, Task

        pipeline_id = self._pipeline_id(op.get("pipeline_name") or "")
        if pipeline_id is None:
            raise RuntimeError(f"pipeline {op.get('pipeline_name')!r} not found for job")
        self.w.jobs.create(
            name=op["name"],
            schedule=CronSchedule(
                quartz_cron_expression=op.get("cron") or "0 0 6 * * ?",
                timezone_id="UTC",
            ),
            tasks=[Task(task_key="refresh", pipeline_task=PipelineTask(pipeline_id=pipeline_id))],
        )

    def create_serving_endpoint(self, op):
        self.w.api_client.do(
            "POST", "/api/2.0/serving-endpoints",
            body={"name": op["name"], "config": op.get("config") or {}},
        )

    def put_ai_gateway(self, op):
        body = {}
        if op.get("usage_tracking"):
            body["usage_tracking_config"] = {"enabled": True}
        if op.get("rate_limits"):
            body["rate_limits"] = op["rate_limits"]
        if op.get("guardrails"):
            body["guardrails"] = op["guardrails"]
        if op.get("fallbacks"):
            body["fallback_config"] = {"enabled": True}
        self.w.api_client.do(
            "PUT", f"/api/2.0/serving-endpoints/{op['endpoint']}/ai-gateway", body=body
        )

    def run(self, op: dict):
        name = str(op.get("op") or "")
        handler = getattr(self, name, None)
        if handler is None or name.startswith("_"):
            raise RuntimeError(f"unknown workspace op {name!r}")
        handler(op)


# ── harness ───────────────────────────────────────────────────────────────────


def _resolve(text: str, variables: dict) -> str:
    return _SLOT_RE.sub(lambda m: str(variables.get(m.group(1), m.group(0))), text)


def _expected_status(task: dict) -> str:
    """What the attempt should aggregate to after the solutions ran.

    Any manual validator (or the review flag) pends host review → ``manual``;
    otherwise every validator is auto and should pass → ``passed``."""
    types = [v.get("type") for v in task.get("validators") or []]
    if "manual" in types or task.get("manual_validation_required"):
        return "manual"
    return "passed"


def main() -> int:  # noqa: PLR0915 - linear operator script, clarity > splitting
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-url", required=True)
    parser.add_argument("--pack", required=True, help="Quest pack YAML path")
    parser.add_argument("--warehouse-id", default=os.getenv("QUEST_SQL_WAREHOUSE_ID", ""))
    parser.add_argument("--token", default=os.getenv("QUEST_APP_TOKEN", ""))
    parser.add_argument("--team-name", default="preflight")
    parser.add_argument("--keep", action="store_true", help="Skip reset/teardown at the end")
    parser.add_argument("--skip-teardown", action="store_true",
                        help="Reset team schemas but keep the event + workspace resources")
    args = parser.parse_args()
    if not args.token:
        raise SystemExit("Provide --token or set QUEST_APP_TOKEN.")
    if not args.warehouse_id:
        raise SystemExit("Provide --warehouse-id or set QUEST_SQL_WAREHOUSE_ID.")

    from databricks.sdk import WorkspaceClient

    app = AppClient(args.app_url, args.token)
    w = WorkspaceClient()
    ops = SolutionOps(w, args.warehouse_id)

    with open(args.pack, "r", encoding="utf-8") as fh:
        pack_text = fh.read()
    pack = yaml.safe_load(pack_text)
    slug = pack["pack"]["slug"]
    stamp = time.strftime("%m%d%H%M")
    event_slug = f"pf-{stamp}"

    print(f"Preflight: {slug} v{pack['pack']['version']} → {args.app_url}")

    # 1. lint (strict) + import
    lint = app.call("POST", "/api/host/quest-packs/lint",
                    {"manifest_yaml": pack_text, "strict": True})
    if not lint.get("ok") or lint.get("warnings"):
        print(json.dumps(lint, indent=1)[:1500])
        raise SystemExit("strict lint not clean — fix the pack first")
    imported = app.call("POST", "/api/host/quest-packs/import", {"manifest_yaml": pack_text})
    pack_version_id = imported.get("pack_version_id") or (imported.get("version") or {}).get("pack_version_id")
    print(f"  imported pack_version_id={pack_version_id}")

    # 2. event + team + join + start
    event = app.call("POST", "/api/host/events", {
        "title": f"Preflight {slug} {stamp}", "slug": event_slug,
        "pack_version_id": pack_version_id,
    })
    event_id = event.get("event_id") or (event.get("event") or {}).get("event_id")
    team = app.call("POST", f"/api/host/events/{event_id}/teams", {"name": args.team_name})
    team_id = team.get("team_id") or (team.get("team") or {}).get("team_id")
    app.call("POST", f"/api/events/{event_id}/join", {"team_id": team_id})
    app.call("POST", f"/api/host/events/{event_id}/ready")
    app.call("POST", f"/api/host/events/{event_id}/start")
    print(f"  event={event_id} team={team_id} status=active")

    # 3. bootstrap: dry-run, then execute
    plan = app.call("POST", f"/api/host/events/{event_id}/resources/plan", {"action": "bootstrap"})
    if plan.get("blockers"):
        print(json.dumps(plan["blockers"], indent=1)[:1200])
        raise SystemExit("bootstrap plan has blockers")
    boot = app.call("POST", f"/api/host/events/{event_id}/resources/bootstrap")
    if not boot.get("ok"):
        print(json.dumps(boot, indent=1)[:1500])
        raise SystemExit("bootstrap failed")
    print(f"  bootstrap ok ({len(boot.get('executed', []))} sql items, "
          f"{len((boot.get('workspace') or {}).get('executed', []))} workspace items)")

    # team namespace variables for solution-step rendering
    resources = app.call("GET", f"/api/host/events/{event_id}/resources")
    target = next((t for t in resources.get("targets", []) if t.get("team_id") == team_id), {})
    variables = {
        "team_catalog": target.get("catalog"), "team_schema": target.get("schema"),
        "team_slug": args.team_name, "event_slug": event_slug,
        "event_catalog": (resources.get("namespace") or {}).get("catalog"),
    }

    def run_sql(statement: str):
        r = w.statement_execution.execute_statement(
            warehouse_id=args.warehouse_id, statement=statement, wait_timeout="50s")
        if "SUCCEEDED" not in str(r.status.state):
            err = r.status.error.message if r.status.error else str(r.status.state)
            raise RuntimeError(f"{err} :: {statement[:120]}")

    # 4. play every task: solutions → submit → assert
    quests = app.call("GET", f"/api/events/{event_id}/quests").get("quests", [])
    quest_ids = {q.get("slug"): q.get("quest_id") for q in quests}
    failures, played = 0, 0
    for quest in pack.get("quests", []):
        detail = app.call("GET", f"/api/events/{event_id}/quests/{quest_ids[quest['slug']]}")
        task_ids = {t.get("slug"): t.get("task_id") for t in detail.get("tasks", [])}
        for task in quest.get("tasks", []):
            label = f"{quest['slug']}/{task['slug']}"
            played += 1
            try:
                for step in task.get("solutions") or []:
                    if "sql" in step:
                        run_sql(_resolve(step["sql"], variables))
                    elif "workspace_op" in step:
                        rendered = json.loads(_resolve(json.dumps(step["workspace_op"]), variables))
                        ops.run(rendered)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"  FAIL  {label} — solution step error: {str(exc)[:160]}")
                continue
            attempt = app.call(
                "POST", f"/api/events/{event_id}/tasks/{task_ids[task['slug']]}/attempts",
                {"submission": {}})
            status = attempt.get("status") or (attempt.get("attempt") or {}).get("status")
            expected = _expected_status(task)
            ok = status == expected or (expected == "manual" and status in ("manual", "pending"))
            if not ok:
                failures += 1
                results = attempt.get("results") or attempt.get("validation_results") or []
                print(f"  FAIL  {label} — expected {expected}, got {status}; "
                      f"{json.dumps(results)[:200]}")
            else:
                print(f"  {expected.upper():6s} {label}")

    # 5. leaderboard sanity: the preflight team scored something
    board = app.call("GET", f"/api/events/{event_id}/leaderboard")
    rows = board.get("leaderboard") or board.get("teams") or []
    our = next((r for r in rows if r.get("team_id") == team_id), None)
    if not our or int(our.get("total_points") or 0) <= 0:
        failures += 1
        print("  FAIL  leaderboard — preflight team has no points")
    else:
        print(f"  leaderboard ok — {our.get('total_points')} pts")

    # 6. reset + teardown rehearsal
    if not args.keep:
        action = "reset" if args.skip_teardown else "teardown"
        plan = app.call("POST", f"/api/host/events/{event_id}/resources/plan", {"action": action})
        if plan.get("blockers"):
            failures += 1
            print(f"  FAIL  {action} plan has blockers")
        else:
            result = app.call("POST", f"/api/host/events/{event_id}/resources/{action}",
                              {"confirm": True})
            print(f"  {action} ok={result.get('ok')}")
            if not result.get("ok"):
                failures += 1

    print(f"\n{'PASS' if failures == 0 else 'FAIL'}: {played} tasks played, {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
