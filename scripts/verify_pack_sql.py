#!/usr/bin/env python3
"""Verify a quest pack's SQL surface against a real warehouse.

The SQL-surface subset of the full preflight harness (PR22): creates a scratch
schema, runs the pack's ``resources.seed_sql``, executes every task's ``sql``
solution steps in order, evaluates every ``sql_assertion`` validator, and drops
the scratch schema. Tables produced by ``workspace_op`` solution steps
(pipelines, dashboards) are out of scope here — their validators are reported
as SKIPPED-BY-DESIGN, and the full preflight covers them.

Usage:
    python scripts/verify_pack_sql.py quest_packs/built_in/genie_deep_dive.yml \
        --warehouse-id <id> --catalog <writable_catalog>

Auth: standard Databricks SDK resolution (profile/env). Exits non-zero if any
seed/solution statement errors or any SQL validator fails.
"""

import argparse
import re
import sys
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "app"))

import yaml  # noqa: E402

_SLOT_RE = re.compile(r"\$\{([a-z_]+)\}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Quest pack YAML path")
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--catalog", required=True, help="Writable catalog for the scratch schema")
    parser.add_argument("--schema", default=None, help="Scratch schema name (default derived)")
    parser.add_argument("--keep", action="store_true", help="Keep the scratch schema afterwards")
    args = parser.parse_args()

    from databricks.sdk import WorkspaceClient

    with open(args.path, "r", encoding="utf-8") as fh:
        pack = yaml.safe_load(fh)
    slug = pack["pack"]["slug"].replace("-", "_")
    schema = args.schema or f"quest_sqlcheck_{slug}"[:60]
    variables = {
        "team_catalog": args.catalog,
        "team_schema": schema,
        "team_slug": "team-sqlcheck",
        "team_prefix": "team_sqlcheck",
        "event_slug": "sqlcheck",
        "event_catalog": args.catalog,
        "event_schema": schema,
    }

    def resolve(text: str) -> str:
        return _SLOT_RE.sub(lambda m: variables.get(m.group(1), m.group(0)), text)

    w = WorkspaceClient()

    def run(sql: str):
        r = w.statement_execution.execute_statement(
            warehouse_id=args.warehouse_id, statement=sql, wait_timeout="50s"
        )
        if "SUCCEEDED" not in str(r.status.state):
            err = r.status.error.message if r.status.error else str(r.status.state)
            raise RuntimeError(f"{err} :: {sql[:140]}")
        rows = r.result.data_array if r.result and r.result.data_array else []
        return rows[0][0] if rows and rows[0] else None

    def expectation_met(value, expect) -> bool:
        op = (expect or {}).get("operator")
        want = (expect or {}).get("value")
        if op is None:
            return value is not None
        if op == "is_true":
            return str(value).lower() == "true"
        if op == "is_false":
            return str(value).lower() == "false"
        if op == "contains":
            return str(want) in str(value)
        if op == "not_contains":
            return str(want) not in str(value)
        try:
            actual, expected = float(value), float(want)
        except (TypeError, ValueError):
            actual, expected = str(value), str(want)  # type: ignore[assignment]
        return {
            "=": actual == expected,
            "!=": actual != expected,
            ">": actual > expected,
            ">=": actual >= expected,
            "<": actual < expected,
            "<=": actual <= expected,
        }.get(op, False)

    # Tables created only by workspace_op steps can't be validated here.
    workspace_only_tasks = set()
    for quest in pack.get("quests", []):
        for task in quest.get("tasks", []):
            steps = task.get("solutions") or []
            if steps and all("workspace_op" in s or "skip" in s for s in steps) and any(
                "workspace_op" in s for s in steps
            ):
                workspace_only_tasks.add(task["slug"])

    failures = 0
    print(f"Pack:    {args.path}")
    print(f"Scratch: {args.catalog}.{schema}")
    run(f"CREATE SCHEMA IF NOT EXISTS {args.catalog}.{schema}")
    try:
        for stmt in (pack.get("resources") or {}).get("seed_sql") or []:
            run(resolve(stmt))
        print("  seeds: ok")
        pipeline_outputs_pending = False
        for quest in pack.get("quests", []):
            for task in quest.get("tasks", []):
                for step in task.get("solutions") or []:
                    if "sql" in step:
                        run(resolve(step["sql"]))
                if task["slug"] in workspace_only_tasks:
                    pipeline_outputs_pending = True
                for v in task.get("validators") or []:
                    if v.get("type") != "sql_assertion":
                        continue
                    vid = f"{task['slug']}/{v.get('id')}"
                    if pipeline_outputs_pending:
                        # Downstream of a workspace_op-produced table.
                        try:
                            value = run(resolve(v["statement"]))
                        except RuntimeError:
                            print(f"  SKIP  {vid} (depends on workspace_op output; full preflight covers it)")
                            continue
                    else:
                        value = run(resolve(v["statement"]))
                    ok = expectation_met(value, v.get("expect"))
                    print(f"  {'PASS' if ok else 'FAIL'}  {vid} -> {value}")
                    if not ok:
                        failures += 1
    finally:
        if not args.keep:
            run(f"DROP SCHEMA IF EXISTS {args.catalog}.{schema} CASCADE")
            print("  scratch schema dropped")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
