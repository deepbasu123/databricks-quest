"""Strict playability lint + ``solutions`` schema (PR20).

Strict mode is the gate that makes "nothing stubbed" machine-checked: every
task in a shipped pack must be provably playable — auto validators carry
executable ``solutions`` steps, SDK/REST validators pair with a manual
fallback, manual-only tasks declare ``manual_validation_required``, and unknown
check names block import instead of warning.
"""

from services.quest_pack_linter import lint_manifest_text


_BASE = """
schema_version: "1.0"
pack:
  slug: strict-probe
  title: Strict Probe
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
{task_body}
"""


def _lint(task_body, strict=True):
    return lint_manifest_text(_BASE.format(task_body=task_body), strict=strict)


_SQL_TASK = """\
        validators:
          - id: v1
            type: sql_assertion
            statement: "SELECT 1"
            expect: {{operator: "=", value: 1}}
{extra}"""


def test_strict_requires_solutions_on_auto_tasks():
    result = _lint(_SQL_TASK.format(extra=""))
    assert not result.ok
    assert any("solutions" in e["message"] for e in result.errors)


def test_strict_satisfied_by_solutions():
    result = _lint(
        _SQL_TASK.format(extra='        solutions:\n          - sql: "SELECT 1"\n')
    )
    assert result.ok, result.errors


def test_non_strict_does_not_require_solutions():
    result = _lint(_SQL_TASK.format(extra=""), strict=False)
    assert result.ok, result.errors


def test_strict_requires_manual_fallback_for_sdk_validators():
    body = """\
        validators:
          - id: v1
            type: databricks_sdk
            check: table_exists
            params: {table: "${team_catalog}.${team_schema}.t"}
        solutions:
          - skip: "covered elsewhere"
"""
    result = _lint(body)
    assert not result.ok
    assert any("manual" in e["message"] for e in result.errors)


def test_strict_passes_sdk_with_fallback_and_solutions():
    body = """\
        manual_validation_required: true
        validators:
          - id: v1
            type: databricks_sdk
            check: table_exists
            params: {table: "${team_catalog}.${team_schema}.t"}
          - id: v2
            type: manual
        solutions:
          - skip: "table created by an earlier task"
"""
    result = _lint(body)
    assert result.ok, result.errors


def test_strict_requires_flag_on_manual_only_tasks():
    body = """\
        validators:
          - id: v1
            type: manual
"""
    result = _lint(body)
    assert not result.ok
    assert any("manual_validation_required" in e["message"] for e in result.errors)


def test_strict_unknown_check_is_error_not_warning():
    body = """\
        manual_validation_required: true
        validators:
          - id: v1
            type: databricks_sdk
            check: not_a_real_check
          - id: v2
            type: manual
        solutions:
          - skip: "n/a"
"""
    assert any(
        "Unknown databricks_sdk check" in e["message"] for e in _lint(body).errors
    )
    non_strict = _lint(body, strict=False)
    assert non_strict.ok
    assert any("Unknown databricks_sdk check" in w["message"] for w in non_strict.warnings)


# ── solutions structural validation (applies in both modes) ──────────────────


def test_solutions_step_must_have_exactly_one_kind():
    body = _SQL_TASK.format(
        extra='        solutions:\n          - {sql: "SELECT 1", skip: "both"}\n'
    )
    result = _lint(body, strict=False)
    assert not result.ok
    assert any("exactly one of" in e["message"] for e in result.errors)


def test_solutions_workspace_op_requires_op_name():
    body = _SQL_TASK.format(
        extra="        solutions:\n          - workspace_op: {name: missing-op}\n"
    )
    result = _lint(body, strict=False)
    assert not result.ok


def test_solutions_empty_list_is_error():
    body = _SQL_TASK.format(extra="        solutions: []\n")
    result = _lint(body, strict=False)
    assert not result.ok


def test_solutions_sql_template_vars_checked():
    body = _SQL_TASK.format(
        extra='        solutions:\n          - sql: "SELECT * FROM ${bogus_var}.t"\n'
    )
    result = _lint(body, strict=False)
    assert result.ok  # unknown template var is a warning, not an error
    assert any("bogus_var" in w["message"] for w in result.warnings)
