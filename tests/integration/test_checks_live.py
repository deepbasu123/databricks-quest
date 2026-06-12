"""Live-workspace verification of every executable SDK check (PR22).

Opt-in (never runs in default ``pytest``): requires

    QUEST_E2E=1
    QUEST_SQL_WAREHOUSE_ID=<warehouse id>
    QUEST_E2E_CATALOG=<writable catalog>
    plus standard Databricks SDK auth (profile/env).

Creates its own fixtures in a ``quest_e2e_checks`` scratch schema through the
real warehouse, runs each check's positive and negative case where the
workspace allows, and tears the scratch schema down. Checks whose fixtures
can't be created cheaply (Lakebase instance, VS endpoint, Agent Bricks tiles)
assert the **negative** path live (the API answers and the check returns a
correct not-found) — their positive paths are covered by the preflight harness
against a provisioned event.

Run: ``QUEST_E2E=1 python -m pytest -m integration tests/integration/ -v``
"""

import os

import pytest

pytestmark = pytest.mark.integration

_REQUIRED_ENV = ("QUEST_E2E", "QUEST_SQL_WAREHOUSE_ID", "QUEST_E2E_CATALOG")
if any(not os.getenv(k) for k in _REQUIRED_ENV):
    pytest.skip(
        "set QUEST_E2E=1, QUEST_SQL_WAREHOUSE_ID, QUEST_E2E_CATALOG to run the live tier",
        allow_module_level=True,
    )

from services import sdk_checks as c  # noqa: E402

CATALOG = os.environ["QUEST_E2E_CATALOG"]
WAREHOUSE = os.environ["QUEST_SQL_WAREHOUSE_ID"]
SCHEMA = "quest_e2e_checks"
NONSENSE = "zz-no-such-resource-zz"


@pytest.fixture(scope="module")
def w():
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient()


@pytest.fixture(scope="module")
def scratch_table(w):
    full = f"{CATALOG}.{SCHEMA}.live_probe"

    def run(sql):
        r = w.statement_execution.execute_statement(
            warehouse_id=WAREHOUSE, statement=sql, wait_timeout="50s"
        )
        assert "SUCCEEDED" in str(r.status.state), r.status

    run(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
    run(f"CREATE OR REPLACE TABLE {full} AS SELECT 1 AS id")
    yield full
    run(f"DROP SCHEMA IF EXISTS {CATALOG}.{SCHEMA} CASCADE")


def test_table_exists_positive_and_negative(w, scratch_table):
    assert c.table_exists(w, {"table": scratch_table}, None)["found"] is True
    assert c.table_exists(w, {"table": f"{CATALOG}.{SCHEMA}.absent"}, None)["found"] is False


def test_dashboard_checks_answer(w):
    # The list API must answer; a nonsense filter must be a clean negative.
    assert c.dashboard_exists_for_team(w, {"name_contains": NONSENSE}, None)["found"] is False
    assert c.dashboard_published(w, {"name_contains": NONSENSE}, None)["found"] is False


def test_genie_checks_answer(w):
    assert c.genie_space_exists(w, {"name_contains": NONSENSE}, None)["found"] is False
    res = c.genie_space_curated(w, {"name_contains": NONSENSE}, None)
    assert res["found"] is False  # resolves to "no space matches"
    res = c.genie_conversation_started(w, {"name_contains": NONSENSE}, None)
    assert res["found"] is False


def test_orchestration_checks_answer(w):
    assert c.job_exists_with_schedule(w, {"name_contains": NONSENSE}, None)["found"] is False
    assert c.pipeline_update_completed(w, {"name_contains": NONSENSE}, None)["found"] is False


def test_serving_and_gateway_checks_answer(w):
    assert c.serving_endpoint_exists(w, {"name_contains": NONSENSE}, None)["found"] is False
    assert c.ai_gateway_configured(w, {"name_contains": NONSENSE}, None)["found"] is False


def test_lakebase_checks_answer(w):
    assert c.lakebase_instance_exists(w, {"name_contains": NONSENSE}, None)["found"] is False
    assert (
        c.lakebase_synced_table_online(w, {"table": f"{CATALOG}.{SCHEMA}.absent"}, None)["found"]
        is False
    )
    assert c.lakebase_app_connected(w, {"name_contains": NONSENSE}, None)["found"] is False


def test_vector_search_checks_answer(w):
    assert c.vector_search_endpoint_exists(w, {"name_contains": NONSENSE}, None)["found"] is False
    assert (
        c.vector_search_index_ready(w, {"index": f"{CATALOG}.{SCHEMA}.absent_idx"}, None)["found"]
        is False
    )


def test_agent_bricks_checks_answer(w):
    # Beta /api/2.0/tiles surface must answer with the documented payload shape.
    assert c.knowledge_assistant_exists(w, {"name_contains": NONSENSE}, None)["found"] is False
    assert c.multi_agent_supervisor_exists(w, {"name_contains": NONSENSE}, None)["found"] is False
