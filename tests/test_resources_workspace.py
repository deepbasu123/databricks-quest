"""Workspace resource provisioning + whole-event teardown (PR21).

Pure / fake-executor tests: the namespace prefix guard, the workspace plan
builder (per-event vs per-team scoping, caps, blockers), the teardown plan's
registry-only deletion rule, and the execute/record loop.
"""

import pytest

from services import namespace as ns
from services.resource_service import (
    ResourceService,
    build_catalog_teardown_plan,
    build_workspace_plan,
    build_workspace_teardown_plan,
    plan_blockers,
)

EVENT = {"event_id": "ev-1", "slug": "summit-day"}
TEAMS = [
    {"team_id": "t1", "name": "Team Red"},
    {"team_id": "t2", "name": "Team Blue"},
]


# ── namespace prefix guard ────────────────────────────────────────────────────


def test_event_resource_prefix_derived_from_slug():
    assert ns.event_resource_prefix(EVENT) == "quest-summit-day-"


def test_resource_name_guard_accepts_any_separator():
    ns.assert_resource_name_in_namespace("quest-summit-day-team-red-gateway", EVENT)
    ns.assert_resource_name_in_namespace("quest_summit_day_vs", EVENT)
    ns.assert_resource_name_in_namespace("Quest Summit Day genie", EVENT)


@pytest.mark.parametrize(
    "name",
    [
        "other-event-endpoint",
        "quest-different-slug-x",
        "",
        "quest-summit-day-../escape",
        "quest-summit-day-a;b",
    ],
)
def test_resource_name_guard_rejects(name):
    with pytest.raises(ns.NamespaceError):
        ns.assert_resource_name_in_namespace(name, EVENT)


# ── workspace plan builder ────────────────────────────────────────────────────


def _resources(*entries):
    return {"workspace": list(entries)}


def test_event_scoped_entry_renders_once():
    plan = build_workspace_plan(
        EVENT, TEAMS,
        _resources({"type": "vector_search_endpoint", "name": "quest-${event_slug}-vs"}),
    )
    assert len(plan) == 1
    assert plan[0]["target"] == "quest-summit-day-vs"
    assert plan[0]["within_namespace"] is True
    assert plan[0]["op"] == "create_vector_search_endpoint"


def test_team_scoped_entry_renders_per_team():
    plan = build_workspace_plan(
        EVENT, TEAMS,
        _resources({
            "type": "serving_endpoint",
            "name": "quest-${event_slug}-${team_slug}-gw",
            "spec": {"config": {"served_entities": []}},
        }),
    )
    assert [i["target"] for i in plan] == [
        "quest-summit-day-team-red-gw",
        "quest-summit-day-team-blue-gw",
    ]
    assert [i["team_id"] for i in plan] == ["t1", "t2"]


def test_out_of_prefix_name_is_blocker():
    plan = build_workspace_plan(
        EVENT, TEAMS, _resources({"type": "genie_space", "name": "my-${event_slug}-genie"})
    )
    assert plan_blockers(plan) == plan


def test_unknown_type_is_blocker():
    plan = build_workspace_plan(
        EVENT, TEAMS, _resources({"type": "gpu_cluster", "name": "quest-${event_slug}-x"})
    )
    assert plan_blockers(plan) == plan


def test_unknown_template_slot_is_blocker():
    plan = build_workspace_plan(
        EVENT, TEAMS, _resources({"type": "genie_space", "name": "quest-${event_slug}-${oops}"})
    )
    assert plan_blockers(plan) == plan


def test_lakebase_cap_is_one():
    plan = build_workspace_plan(
        EVENT, TEAMS,
        _resources({
            "type": "lakebase_instance",
            "name": "quest-${event_slug}-${team_slug}-pg",  # per-team → 2 > cap 1
        }),
    )
    blockers = plan_blockers(plan)
    assert len(plan) == 2 and len(blockers) == 1
    assert "cap" in blockers[0]["error"].lower() or "more than" in blockers[0]["error"]


def test_spec_templates_resolve():
    plan = build_workspace_plan(
        EVENT, TEAMS,
        _resources({
            "type": "genie_space",
            "name": "quest-${event_slug}-${team_slug}-genie",
            "spec": {"tables": ["${team_catalog}.${team_schema}.gold"]},
        }),
    )
    assert plan[0]["spec"]["tables"][0].endswith(".gold")
    assert "${" not in plan[0]["spec"]["tables"][0]


# ── teardown plans ────────────────────────────────────────────────────────────


def test_teardown_only_registry_recorded_workspace_resources():
    registry = [
        {"fqn": "quest-summit-day-vs", "resource_type": "vector_search_endpoint", "status": "active"},
        {"fqn": "quest_summit_day.team_red", "resource_type": "schema", "status": "active"},
        {"fqn": "quest-summit-day-old", "resource_type": "serving_endpoint", "status": "removed"},
        {"fqn": "someone-elses-endpoint", "resource_type": "serving_endpoint", "status": "active"},
    ]
    plan = build_workspace_teardown_plan(EVENT, registry)
    # schema rows and already-removed rows are excluded entirely.
    assert [i["target"] for i in plan] == ["quest-summit-day-vs", "someone-elses-endpoint"]
    # the foreign name is present but flagged as a blocker, so execution refuses.
    blockers = plan_blockers(plan)
    assert [b["target"] for b in blockers] == ["someone-elses-endpoint"]


def test_catalog_teardown_targets_only_namespace_catalog():
    plan = build_catalog_teardown_plan(EVENT, TEAMS)
    assert len(plan) == 1
    assert plan[0]["sql"] == "DROP CATALOG IF EXISTS quest_summit_day CASCADE"


def test_catalog_teardown_refuses_reserved():
    ev = {"event_id": "e", "slug": "x", "config_json": {"resource_namespace": {"catalog": "main"}}}
    with pytest.raises(ns.NamespaceError):
        build_catalog_teardown_plan(ev, [])


# ── execute_workspace_plan ────────────────────────────────────────────────────


class _FakeRepo:
    def __init__(self):
        self.rows = []

    def upsert(self, **kwargs):
        self.rows.append(kwargs)


class _FakeExecutor:
    def __init__(self, fail_targets=()):
        self.fail_targets = set(fail_targets)
        self.executed = []

    def execute(self, item):
        self.executed.append(item)
        if item["target"] in self.fail_targets:
            raise RuntimeError("quota exceeded")


def _plan_one():
    return build_workspace_plan(
        EVENT, TEAMS,
        _resources({"type": "serving_endpoint", "name": "quest-${event_slug}-${team_slug}-gw"}),
    )


def test_execute_workspace_plan_records_active():
    repo, execu = _FakeRepo(), _FakeExecutor()
    svc = ResourceService(repo=repo)
    result = svc.execute_workspace_plan(EVENT, _plan_one(), execu, created_by="host")
    assert result["ok"] is True
    assert len(execu.executed) == 2
    assert {r["status"] for r in repo.rows} == {"active"}
    assert {r["resource_type"] for r in repo.rows} == {"serving_endpoint"}


def test_execute_workspace_plan_records_per_item_failure():
    repo = _FakeRepo()
    execu = _FakeExecutor(fail_targets={"quest-summit-day-team-red-gw"})
    svc = ResourceService(repo=repo)
    result = svc.execute_workspace_plan(EVENT, _plan_one(), execu, created_by="host")
    assert result["ok"] is False
    statuses = {r["fqn"]: r["status"] for r in repo.rows}
    assert statuses["quest-summit-day-team-red-gw"] == "failed"
    assert statuses["quest-summit-day-team-blue-gw"] == "active"


def test_execute_workspace_plan_refuses_blockers():
    repo, execu = _FakeRepo(), _FakeExecutor()
    svc = ResourceService(repo=repo)
    plan = build_workspace_plan(
        EVENT, TEAMS, _resources({"type": "genie_space", "name": "evil-${event_slug}"})
    )
    result = svc.execute_workspace_plan(EVENT, plan, execu)
    assert result["ok"] is False
    assert execu.executed == [] and repo.rows == []


def test_execute_workspace_plan_records_removed_on_delete():
    repo, execu = _FakeRepo(), _FakeExecutor()
    svc = ResourceService(repo=repo)
    registry = [
        {"fqn": "quest-summit-day-vs", "resource_type": "vector_search_endpoint", "status": "active"}
    ]
    plan = build_workspace_teardown_plan(EVENT, registry)
    result = svc.execute_workspace_plan(EVENT, plan, execu)
    assert result["ok"] is True
    assert repo.rows[0]["status"] == "removed"


# ── linter: resources.workspace ───────────────────────────────────────────────


def _lint_resources(workspace_yaml):
    from services.quest_pack_linter import lint_manifest_text

    return lint_manifest_text(f"""
schema_version: "1.0"
pack:
  slug: ws-probe
  title: WS Probe
  version: 1.0.0
resources:
  workspace:
{workspace_yaml}
quests:
  - slug: q1
    title: Q
    tasks:
      - slug: t1
        title: T
        objective: o
        manual_validation_required: true
        validators:
          - id: v1
            type: manual
""")


def test_linter_accepts_valid_workspace_entry():
    result = _lint_resources(
        '    - type: serving_endpoint\n      name: "quest-${event_slug}-${team_slug}-gw"\n'
    )
    assert result.ok, result.errors


def test_linter_errors_on_unknown_type_and_missing_event_slug():
    result = _lint_resources(
        '    - type: gpu_cluster\n      name: "my-endpoint"\n'
    )
    messages = " ".join(e["message"] for e in result.errors)
    assert "Unknown workspace resource type" in messages
    assert "${event_slug}" in messages
