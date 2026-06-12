"""Regression tests for the pre-merge stack review fixes.

Each test pins a confirmed finding from the review of PR17–CP7:

1. team_slug rendering matches the validator-variable derivation (underscores)
2. Genie space listing follows pagination everywhere
3. genie_space_curated rejects an empty instructions container
4. workspace creates are idempotent / deletes tolerate missing resources
5. drop_catalog is recorded as 'removed' in the resource registry
6. teardown failed-create rows resolve cleanly (delete tolerates missing)
"""

from types import SimpleNamespace

from services import namespace as ns
from services.resource_service import (
    ResourceService,
    SDKWorkspaceExecutor,
    build_catalog_teardown_plan,
    build_workspace_plan,
)
from services.sdk_checks import genie_space_curated, iter_genie_spaces

EVENT = {"event_id": "ev-1", "slug": "summit-day"}
TEAMS = [{"team_id": "t1", "name": "Red Team"}]


# ── 1. slug convention parity ────────────────────────────────────────────────


def test_team_slug_matches_validator_variable_convention():
    plan = build_workspace_plan(
        EVENT, TEAMS,
        {"workspace": [{"type": "serving_endpoint", "name": "quest-${event_slug}-${team_slug}-gw"}]},
    )
    # Multi-word team 'Red Team' → sanitize_identifier → 'red_team' (underscores),
    # exactly what main._build_validator_variables resolves for ${team_slug}.
    assert plan[0]["target"] == "quest-summit-day-red_team-gw"
    assert plan[0]["within_namespace"] is True
    assert ns.sanitize_identifier("Red Team") == "red_team"


# ── 2. Genie pagination ───────────────────────────────────────────────────────


class _PagedGenie:
    def __init__(self):
        self.calls = []

    def list_spaces(self, page_token=None):
        self.calls.append(page_token)
        if page_token is None:
            return SimpleNamespace(
                spaces=[SimpleNamespace(title="Other Space", space_id="s-0")],
                next_page_token="p2",
            )
        return SimpleNamespace(
            spaces=[SimpleNamespace(title="team_red sales genie", space_id="s-9")],
            next_page_token=None,
        )


def test_iter_genie_spaces_follows_pagination():
    client = SimpleNamespace(genie=_PagedGenie())
    titles = [getattr(s, "title") for s in iter_genie_spaces(client)]
    assert titles == ["Other Space", "team_red sales genie"]
    assert client.genie.calls == [None, "p2"]


def test_iter_genie_spaces_handles_unpaginated_fakes():
    class _Flat:
        def list_spaces(self):  # no page_token kwarg, no next_page_token
            return SimpleNamespace(spaces=[SimpleNamespace(title="only", space_id="s")])

    client = SimpleNamespace(genie=_Flat())
    assert len(list(iter_genie_spaces(client))) == 1


# ── 3. empty instructions container must not pass ─────────────────────────────


class _CuratedGenie:
    def __init__(self, serialized):
        self._serialized = serialized

    def list_spaces(self):
        return SimpleNamespace(
            spaces=[SimpleNamespace(title="team_red sales genie", space_id="s-1")]
        )

    def get_space(self, space_id, include_serialized_space=None):
        return SimpleNamespace(serialized_space=self._serialized)


def test_empty_instructions_container_fails_curation():
    import json

    client = SimpleNamespace(
        genie=_CuratedGenie(json.dumps({"instructions": {"text_instructions": []}}))
    )
    res = genie_space_curated(
        client, {"name_contains": "team_red", "require_instructions": True}, None
    )
    assert res["found"] is False
    assert "instructions" in res["detail"]


def test_real_instructions_still_pass_curation():
    import json

    client = SimpleNamespace(
        genie=_CuratedGenie(
            json.dumps({"instructions": {"text_instructions": [{"id": "1", "content": "Revenue means net."}]}})
        )
    )
    res = genie_space_curated(
        client, {"name_contains": "team_red", "require_instructions": True}, None
    )
    assert res["found"] is True


# ── 4. executor idempotency / tolerant deletes ────────────────────────────────


def _executor_with(create_exc=None, delete_exc=None):
    class _Serving:
        def delete(self, name):
            if delete_exc:
                raise delete_exc

    class _API:
        def do(self, method, path, body=None):
            if create_exc:
                raise create_exc

    client = SimpleNamespace(serving_endpoints=_Serving(), api_client=_API())
    return SDKWorkspaceExecutor(client_factory=lambda: client)


def test_create_already_exists_is_success():
    execu = _executor_with(create_exc=RuntimeError("Endpoint with name x already exists"))
    execu.execute({"op": "create_serving_endpoint", "target": "x", "spec": {}})  # no raise


def test_delete_missing_is_success():
    execu = _executor_with(delete_exc=RuntimeError("RESOURCE_DOES_NOT_EXIST: not found"))
    execu.execute({"op": "delete_serving_endpoint", "target": "x", "spec": {}})  # no raise


def test_other_errors_still_raise():
    import pytest

    execu = _executor_with(create_exc=RuntimeError("PERMISSION_DENIED"))
    with pytest.raises(RuntimeError):
        execu.execute({"op": "create_serving_endpoint", "target": "x", "spec": {}})


# ── 5. drop_catalog recorded as removed ───────────────────────────────────────


class _Repo:
    def __init__(self):
        self.rows = []

    def upsert(self, **kw):
        self.rows.append(kw)


def test_drop_catalog_recorded_as_removed():
    repo = _Repo()
    svc = ResourceService(repo=repo)
    plan = build_catalog_teardown_plan(EVENT, TEAMS)
    result = svc.execute_plan(EVENT, plan, lambda sql, t: [], created_by="host")
    assert result["ok"] is True
    assert repo.rows[0]["status"] == "removed"
    assert repo.rows[0]["resource_type"] == "catalog"
