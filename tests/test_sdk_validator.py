"""Executable ``databricks_sdk`` validator + ``sdk_checks`` registry (PR-pilot C).

DB- and SDK-free: a fake duck-typed WorkspaceClient is injected so the checks
run their real lookup logic without the SDK installed. Covers the four outcome
classes the validator must produce:

- artefact found            → ``passed``
- artefact not found        → ``failed``
- unknown check / bad param → ``error`` (authoring bug, via ValidatorConfigError)
- check can't run at runtime → ``manual`` (host-review fallback, never a block)
"""

import pytest

from services.sdk_checks import (
    SDK_CHECKS,
    SDKCheckConfigError,
    dashboard_exists_for_team,
    known_checks,
    table_exists,
)
from validators.base import ERROR, FAILED, MANUAL, PASSED, ValidationContext
from validators.databricks_sdk import DatabricksSDKValidator


# ── fake workspace client ─────────────────────────────────────────────────────


class _Named:
    def __init__(self, name, **extra):
        self.display_name = name
        for k, v in extra.items():
            setattr(self, k, v)


class _Lakeview:
    def __init__(self, names):
        self._names = names

    def list(self):
        return [_Named(n) for n in self._names]


class _Tables:
    def __init__(self, present):
        self._present = set(present)

    def get(self, full_name):
        if full_name not in self._present:
            raise RuntimeError("NOT_FOUND")
        return _Named(full_name)


class FakeClient:
    """Duck-typed WorkspaceClient with just the surfaces the checks probe."""

    def __init__(self, dashboards=None, tables=None):
        if dashboards is not None:
            self.lakeview = _Lakeview(dashboards)
        if tables is not None:
            self.tables = _Tables(tables)


def _validator(client=None, *, raise_client=None):
    def factory():
        if raise_client is not None:
            raise raise_client
        return client

    return DatabricksSDKValidator(client_factory=factory)


def _ctx(check, params=None, variables=None):
    return ValidationContext(
        validator_id="v1",
        type="databricks_sdk",
        config={"check": check, "params": params or {}},
        variables=variables or {},
    )


# ── outcome classes ───────────────────────────────────────────────────────────


def test_passed_when_artifact_found():
    v = _validator(FakeClient(dashboards=["Team Red Sales", "Other"]))
    out = v.validate(_ctx("dashboard_exists_for_team", {"name_contains": "Team Red"}))
    assert out.status == PASSED
    assert out.evidence["check"] == "dashboard_exists_for_team"


def test_failed_when_artifact_absent():
    v = _validator(FakeClient(dashboards=["Other dashboard"]))
    out = v.validate(_ctx("dashboard_exists_for_team", {"name_contains": "Team Red"}))
    assert out.status == FAILED
    # Player-safe message, no internals.
    assert "workspace" in out.public_message.lower()


def test_unknown_check_is_config_error():
    from validators.base import ValidatorConfigError

    v = _validator(FakeClient(dashboards=[]))
    with pytest.raises(ValidatorConfigError):
        v.validate(_ctx("does_not_exist"))


def test_missing_check_name_is_config_error():
    from validators.base import ValidatorConfigError

    v = _validator(FakeClient(dashboards=[]))
    with pytest.raises(ValidatorConfigError):
        v.validate(_ctx("", {}))


def test_client_unavailable_routes_to_manual():
    v = _validator(raise_client=RuntimeError("no creds"))
    out = v.validate(_ctx("dashboard_exists_for_team", {"name_contains": "x"}))
    assert out.status == MANUAL
    assert out.evidence["stage"] == "client"
    # Raw exception text stays host-only.
    assert "no creds" in (out.private_message or "")
    assert "no creds" not in out.public_message


def test_runtime_check_failure_routes_to_manual():
    # A client with no lakeview/dashboards surface → _list_dashboards raises at
    # runtime → host review, not a hard error.
    v = _validator(FakeClient())  # no dashboards/tables attrs
    out = v.validate(_ctx("dashboard_exists_for_team", {"name_contains": "x"}))
    assert out.status == MANUAL
    assert out.evidence["stage"] == "execute"


def test_required_param_missing_is_config_error():
    from validators.base import ValidatorConfigError

    v = _validator(FakeClient(tables=[]))
    with pytest.raises(ValidatorConfigError):
        v.validate(_ctx("table_exists", {}))  # 'table' param required


# ── the engine maps a ValidatorConfigError to a host-visible error ────────────


def test_engine_maps_config_error_to_error_status():
    from services.validation_engine import ValidationEngine

    engine = ValidationEngine(sdk_validator=_validator(FakeClient(dashboards=[])))
    out = engine.run_validator(
        {
            "validator_id": "v1",
            "type": "databricks_sdk",
            "config_json": {"check": "nope"},
            "expected_json": None,
            "timeout_seconds": 30,
        },
        submission={},
        variables={},
    )
    assert out.status == ERROR


# ── template-variable resolution in params ────────────────────────────────────


def test_param_slots_resolved_from_variables():
    captured = {}

    def fake_check(client, params, ctx):
        captured.update(params)
        return {"found": True, "detail": "ok", "evidence": {}}

    v = DatabricksSDKValidator(
        client_factory=lambda: FakeClient(dashboards=[]),
        checks={"probe": fake_check},
    )
    out = v.validate(
        _ctx("probe", {"name_contains": "${team_slug}"}, {"team_slug": "team_red"})
    )
    assert out.status == PASSED
    assert captured["name_contains"] == "team_red"


def test_unresolved_optional_param_is_dropped():
    captured = {}

    def fake_check(client, params, ctx):
        captured.update(params)
        return {"found": True, "detail": "ok", "evidence": {}}

    v = DatabricksSDKValidator(
        client_factory=lambda: FakeClient(dashboards=[]),
        checks={"probe": fake_check},
    )
    # ${event_start} not provided → the filter is dropped, not failed.
    v.validate(_ctx("probe", {"created_after": "${event_start}", "keep": "yes"}))
    assert "created_after" not in captured
    assert captured["keep"] == "yes"


# ── sdk_checks registry directly ──────────────────────────────────────────────


def test_table_exists_found_and_missing():
    client = FakeClient(tables=["main.gameday.gold"])
    assert table_exists(client, {"table": "main.gameday.gold"}, None)["found"] is True
    assert table_exists(client, {"table": "main.gameday.missing"}, None)["found"] is False


def test_table_exists_requires_table_param():
    with pytest.raises(SDKCheckConfigError):
        table_exists(FakeClient(tables=[]), {}, None)


def test_dashboard_check_name_filter():
    client = FakeClient(dashboards=["Red KPI", "Blue KPI"])
    assert dashboard_exists_for_team(client, {"name_contains": "Red"}, None)["found"]
    assert not dashboard_exists_for_team(client, {"name_contains": "Green"}, None)["found"]


def test_known_checks_lists_registry():
    names = known_checks()
    assert "dashboard_exists_for_team" in names
    assert "table_exists" in names
    assert set(names) == set(SDK_CHECKS.keys())


# ── wave-1 checks: serving endpoints / AI Gateway / Lakebase / Vector Search ──

from types import SimpleNamespace

from services.sdk_checks import (
    KNOWN_PARAMS,
    REQUIRED_PARAMS,
    ai_gateway_configured,
    lakebase_instance_exists,
    lakebase_synced_table_online,
    serving_endpoint_exists,
    vector_search_endpoint_exists,
    vector_search_index_ready,
)


class _ServingEndpoints:
    def __init__(self, endpoints):
        self._endpoints = list(endpoints)

    def list(self):
        return list(self._endpoints)

    def get(self, name):
        for ep in self._endpoints:
            if getattr(ep, "name", None) == name:
                return ep
        raise RuntimeError("NOT_FOUND")


class _Database:
    def __init__(self, instances=None, synced=None):
        self._instances = list(instances or [])
        self._synced = dict(synced or {})

    def list_database_instances(self):
        return list(self._instances)

    def get_synced_database_table(self, full_name):
        if full_name not in self._synced:
            raise RuntimeError("NOT_FOUND")
        return self._synced[full_name]


class _VSEndpoints:
    def __init__(self, endpoints):
        self._endpoints = list(endpoints)

    def list_endpoints(self):
        return list(self._endpoints)


class _VSIndexes:
    def __init__(self, indexes):
        self._indexes = dict(indexes)

    def get_index(self, name):
        if name not in self._indexes:
            raise RuntimeError("NOT_FOUND")
        return self._indexes[name]


def _endpoint(name, ready="READY", task="llm/v1/chat", ai_gateway=None):
    return SimpleNamespace(
        name=name,
        state=SimpleNamespace(ready=ready),
        task=task,
        ai_gateway=ai_gateway,
    )


def _gateway(**features):
    base = dict(
        rate_limits=None,
        guardrails=None,
        usage_tracking_config=None,
        inference_table_config=None,
        fallback_config=None,
    )
    base.update(features)
    return SimpleNamespace(**base)


def test_serving_endpoint_exists_ready_filter():
    client = FakeClient()
    client.serving_endpoints = _ServingEndpoints(
        [_endpoint("team-red-gateway", ready="READY"), _endpoint("team-blue-gateway", ready="NOT_READY")]
    )
    assert serving_endpoint_exists(client, {"name_contains": "team-red"}, None)["found"]
    res = serving_endpoint_exists(client, {"name_contains": "team-blue"}, None)
    assert res["found"] is False
    # Existence is enough when readiness is waived.
    assert serving_endpoint_exists(
        client, {"name_contains": "team-blue", "require_ready": False}, None
    )["found"]


def test_serving_endpoint_exists_task_filter_and_exact_name():
    client = FakeClient()
    client.serving_endpoints = _ServingEndpoints(
        [_endpoint("team-red-gateway", task="llm/v1/embeddings")]
    )
    assert serving_endpoint_exists(client, {"name": "team-red-gateway"}, None)["found"]
    assert not serving_endpoint_exists(
        client, {"name": "team-red-gateway", "task_contains": "chat"}, None
    )["found"]


def test_serving_endpoint_exists_requires_name_or_contains():
    with pytest.raises(SDKCheckConfigError):
        serving_endpoint_exists(FakeClient(), {}, None)


def test_ai_gateway_configured_flags():
    gw = _gateway(
        rate_limits=[SimpleNamespace(calls=10)],
        usage_tracking_config=SimpleNamespace(enabled=True),
    )
    client = FakeClient()
    client.serving_endpoints = _ServingEndpoints([_endpoint("team-red-gateway", ai_gateway=gw)])
    ok = ai_gateway_configured(
        client,
        {"name": "team-red-gateway", "require_rate_limits": True, "require_usage_tracking": True},
        None,
    )
    assert ok["found"] is True
    assert "rate limits" in ok["evidence"]["features_present"]
    missing = ai_gateway_configured(
        client, {"name": "team-red-gateway", "require_guardrails": True}, None
    )
    assert missing["found"] is False
    assert missing["evidence"]["missing"] == ["guardrails"]


def test_ai_gateway_configured_no_gateway_fails():
    client = FakeClient()
    client.serving_endpoints = _ServingEndpoints([_endpoint("plain", ai_gateway=None)])
    assert ai_gateway_configured(client, {"name": "plain"}, None)["found"] is False


def test_ai_gateway_configured_resolves_name_contains():
    gw = _gateway(guardrails=SimpleNamespace(input=SimpleNamespace(pii=True), output=None))
    client = FakeClient()
    client.serving_endpoints = _ServingEndpoints([_endpoint("team-red-gateway", ai_gateway=gw)])
    res = ai_gateway_configured(client, {"name_contains": "red", "require_guardrails": True}, None)
    assert res["found"] is True


def test_lakebase_instance_exists_state_filter():
    client = FakeClient()
    client.database = _Database(
        instances=[
            SimpleNamespace(name="team-red", state="AVAILABLE"),
            SimpleNamespace(name="team-blue", state="STARTING"),
        ]
    )
    assert lakebase_instance_exists(client, {"name_contains": "red"}, None)["found"]
    assert not lakebase_instance_exists(client, {"name_contains": "blue"}, None)["found"]
    assert lakebase_instance_exists(
        client, {"name_contains": "blue", "require_available": False}, None
    )["found"]


def test_lakebase_synced_table_online_states():
    online = SimpleNamespace(
        data_synchronization_status=SimpleNamespace(detailed_state="ONLINE_TRIGGERED_UPDATE")
    )
    provisioning = SimpleNamespace(
        data_synchronization_status=SimpleNamespace(detailed_state="PROVISIONING")
    )
    client = FakeClient()
    client.database = _Database(
        synced={"cat.sch.products": online, "cat.sch.pending": provisioning}
    )
    assert lakebase_synced_table_online(client, {"table": "cat.sch.products"}, None)["found"]
    assert not lakebase_synced_table_online(client, {"table": "cat.sch.pending"}, None)["found"]
    assert not lakebase_synced_table_online(client, {"table": "cat.sch.absent"}, None)["found"]
    with pytest.raises(SDKCheckConfigError):
        lakebase_synced_table_online(client, {}, None)


def test_vector_search_endpoint_exists_online_filter():
    client = FakeClient()
    client.vector_search_endpoints = _VSEndpoints(
        [
            SimpleNamespace(name="event-vs", endpoint_status=SimpleNamespace(state="ONLINE")),
            SimpleNamespace(name="warming-vs", endpoint_status=SimpleNamespace(state="PROVISIONING")),
        ]
    )
    assert vector_search_endpoint_exists(client, {"name": "event-vs"}, None)["found"]
    assert not vector_search_endpoint_exists(client, {"name": "warming-vs"}, None)["found"]


def test_vector_search_index_ready_min_rows():
    client = FakeClient()
    client.vector_search_indexes = _VSIndexes(
        {
            "cat.sch.docs_idx": SimpleNamespace(
                status=SimpleNamespace(ready=True, indexed_row_count=42)
            )
        }
    )
    assert vector_search_index_ready(client, {"index": "cat.sch.docs_idx"}, None)["found"]
    assert not vector_search_index_ready(
        client, {"index": "cat.sch.docs_idx", "min_rows": 100}, None
    )["found"]
    assert not vector_search_index_ready(client, {"index": "cat.sch.missing"}, None)["found"]


def test_new_checks_route_to_manual_when_surface_missing():
    # Bare client (no serving_endpoints/database/vector_search attrs) → the
    # check raises at runtime → validator routes to host review.
    v = _validator(FakeClient())
    for check, params in [
        ("serving_endpoint_exists", {"name": "x"}),
        ("lakebase_instance_exists", {"name": "x"}),
        ("lakebase_synced_table_online", {"table": "a.b.c"}),
        ("vector_search_endpoint_exists", {"name": "x"}),
        ("vector_search_index_ready", {"index": "a.b.c"}),
    ]:
        out = v.validate(_ctx(check, params))
        assert out.status == MANUAL, check


def test_param_contracts_cover_every_check():
    assert set(KNOWN_PARAMS.keys()) == set(SDK_CHECKS.keys())
    assert set(REQUIRED_PARAMS.keys()) <= set(SDK_CHECKS.keys())


# ── wave-2 checks: Genie curation/conversations, Apps, Agent Bricks tiles ────

import json as _json

from services.sdk_checks import (
    genie_conversation_started,
    genie_space_curated,
    knowledge_assistant_exists,
    lakebase_app_connected,
    multi_agent_supervisor_exists,
)

# Serialized-space payload shape verified against a live export (SDK 0.94).
_SERIALIZED_SPACE = _json.dumps(
    {
        "version": 1,
        "instructions": {"text_instructions": [{"id": "1", "content": "Revenue means net_revenue."}]},
        "config": {"sample_questions": [{"id": "q1", "question": "Revenue by region?"}, {"id": "q2", "question": "Top product?"}]},
        "data_sources": {"tables": [{"identifier": "cat.sch.orders"}]},
    }
)


class _Genie:
    def __init__(self, spaces=None, serialized=None, conversations=None):
        self._spaces = spaces or []
        self._serialized = serialized
        self._conversations = conversations or []

    def list_spaces(self):
        return SimpleNamespace(spaces=self._spaces)

    def get_space(self, space_id, include_serialized_space=None):
        return SimpleNamespace(space_id=space_id, serialized_space=self._serialized)

    def list_conversations(self, space_id, include_all=None):
        return SimpleNamespace(conversations=self._conversations)


def _genie_client(serialized=_SERIALIZED_SPACE, conversations=None):
    client = FakeClient()
    client.genie = _Genie(
        spaces=[SimpleNamespace(title="Team Red Genie", space_id="s-1")],
        serialized=serialized,
        conversations=conversations or [],
    )
    return client


def test_genie_space_curated_meets_bar():
    res = genie_space_curated(
        _genie_client(),
        {"name_contains": "Team Red", "require_instructions": True, "min_sample_questions": 2, "min_tables": 1},
        None,
    )
    assert res["found"] is True
    assert res["evidence"]["sample_questions"] == 2


def test_genie_space_curated_below_bar_fails():
    res = genie_space_curated(
        _genie_client(),
        {"name_contains": "Team Red", "min_sample_questions": 5},
        None,
    )
    assert res["found"] is False
    assert "sample questions (2/5)" in res["detail"]


def test_genie_space_curated_unrecognized_payload_routes_to_manual():
    client = _genie_client(serialized=_json.dumps({"version": 1, "mystery": []}))
    v = _validator(client)
    out = v.validate(
        _ctx("genie_space_curated", {"name_contains": "Team Red", "require_instructions": True})
    )
    assert out.status == MANUAL


def test_genie_space_curated_no_match_fails():
    res = genie_space_curated(_genie_client(), {"name_contains": "Blue"}, None)
    assert res["found"] is False


def test_genie_conversation_started_counts_and_filters():
    convs = [
        SimpleNamespace(conversation_id="c1", created_timestamp=1_700_000_000_000),
        SimpleNamespace(conversation_id="c2", created_timestamp=1_900_000_000_000),
    ]
    client = _genie_client(conversations=convs)
    assert genie_conversation_started(client, {"name_contains": "Team Red"}, None)["found"]
    res = genie_conversation_started(
        client,
        {"name_contains": "Team Red", "created_after": "2026-01-01T00:00:00+00:00", "min_conversations": 2},
        None,
    )
    assert res["found"] is False  # only c2 is after the cutoff
    assert res["evidence"]["conversations"] == 1


def test_lakebase_app_connected_requires_database_resource():
    client = FakeClient()
    client.apps = SimpleNamespace(
        list=lambda: [
            SimpleNamespace(
                name="team-red-app",
                resources=[SimpleNamespace(database=SimpleNamespace(instance_name="pg"))],
                compute_status=SimpleNamespace(state="ACTIVE"),
            ),
            SimpleNamespace(name="team-blue-app", resources=[], compute_status=SimpleNamespace(state="ACTIVE")),
        ]
    )
    assert lakebase_app_connected(client, {"name_contains": "red"}, None)["found"]
    assert not lakebase_app_connected(client, {"name_contains": "blue"}, None)["found"]


class _TilesAPIClient:
    def __init__(self, tiles):
        self._tiles = tiles

    def do(self, method, path):
        assert method == "GET" and path == "/api/2.0/tiles"
        return {"tiles": self._tiles}


def _tiles_client(tiles):
    client = FakeClient()
    client.api_client = _TilesAPIClient(tiles)
    return client


# Tile shape verified live against the beta /api/2.0/tiles surface.
_TILES = [
    {"tile_id": "ka-1", "name": "team-red-ka", "tile_type": "KA", "serving_endpoint_name": "ka-1-endpoint"},
    {"tile_id": "mas-1", "name": "team-red-concierge", "tile_type": "MAS", "serving_endpoint_name": "mas-1-endpoint"},
    {"tile_id": "ka-2", "name": "draft-ka", "tile_type": "KA", "serving_endpoint_name": ""},
]


def test_knowledge_assistant_exists_filters_type_and_endpoint():
    client = _tiles_client(_TILES)
    assert knowledge_assistant_exists(client, {"name_contains": "team-red"}, None)["found"]
    # MAS tile does not satisfy the KA check even though the name matches.
    assert not knowledge_assistant_exists(client, {"name": "team-red-concierge"}, None)["found"]
    # Tile without a serving endpoint fails unless require_endpoint is waived.
    assert not knowledge_assistant_exists(client, {"name": "draft-ka"}, None)["found"]
    assert knowledge_assistant_exists(client, {"name": "draft-ka", "require_endpoint": False}, None)["found"]


def test_multi_agent_supervisor_exists():
    client = _tiles_client(_TILES)
    assert multi_agent_supervisor_exists(client, {"name_contains": "concierge"}, None)["found"]
    assert not multi_agent_supervisor_exists(client, {"name_contains": "ka"}, None)["found"]


def test_agent_bricks_unrecognized_payload_routes_to_manual():
    client = FakeClient()
    client.api_client = SimpleNamespace(do=lambda m, p: {"unexpected": True})
    v = _validator(client)
    out = v.validate(_ctx("knowledge_assistant_exists", {"name_contains": "x"}))
    assert out.status == MANUAL


# ── linter enforces the check/param contracts ─────────────────────────────────


_PACK_TEMPLATE = """
schema_version: "1.0"
pack:
  slug: lint-probe
  title: Lint Probe
  version: 1.0.0
  owner: pilot@databricks.com
quests:
  - slug: q1
    title: Quest
    tasks:
      - slug: t1
        title: Task
        objective: Probe
        points: 100
        manual_validation_required: true
        validators:
          - id: v-manual
            type: manual
          - id: v-sdk
            type: databricks_sdk
            check: {check}
            params:
{params}
"""


def _lint_pack(check, params_yaml="              {}"):
    from services.quest_pack_linter import lint_manifest_text

    return lint_manifest_text(_PACK_TEMPLATE.format(check=check, params=params_yaml))


def test_linter_warns_on_unknown_check():
    result = _lint_pack("does_not_exist")
    assert result.ok
    assert any("Unknown databricks_sdk check" in w["message"] for w in result.warnings)


def test_linter_errors_on_missing_required_param():
    result = _lint_pack("table_exists")
    assert not result.ok
    assert any("requires param 'table'" in e["message"] for e in result.errors)


def test_linter_errors_on_missing_any_of_params():
    result = _lint_pack("serving_endpoint_exists")
    assert not result.ok
    assert any("requires one of" in e["message"] for e in result.errors)


def test_linter_accepts_valid_check_params():
    result = _lint_pack(
        "serving_endpoint_exists",
        '              name_contains: "${team_slug}-gateway"',
    )
    assert result.ok, result.errors
    assert not result.warnings


def test_linter_warns_on_unused_param():
    result = _lint_pack(
        "table_exists",
        '              table: "${team_catalog}.${team_schema}.gold"\n'
        "              bogus: 1",
    )
    assert result.ok
    assert any("does not use param 'bogus'" in w["message"] for w in result.warnings)
