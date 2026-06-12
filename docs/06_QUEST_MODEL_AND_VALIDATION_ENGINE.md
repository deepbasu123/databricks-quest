# 06 — Quest Model and Validation Engine

> **Authoring a pack? Start with [`AUTHORING_QUEST_PACKS.md`](AUTHORING_QUEST_PACKS.md)** — the single guided scaffold→author→lint→import→version walkthrough. This file is the model + validation-engine deep dive behind it.

## Why validation is the product

A GameDay succeeds or fails on whether participants trust the scoring.

System-table inference alone is too blunt. For example, system tables can tell that a user ran a query or created a pipeline, but they cannot reliably prove that a team:

- produced the required business output
- used the intended governance pattern
- created the correct object in the correct schema
- passed data quality constraints
- optimized cost or performance
- handled an incident scenario correctly
- wrote code that produces the expected result

Therefore, validation must become a first-class product domain.

## Core domain concepts

### Quest Pack

A versioned package of event content.

Fields:

- `pack_id`
- `version`
- `title`
- `description`
- `audience`
- `duration_minutes`
- `difficulty`
- `scenario_narrative`
- `learning_objectives`
- `required_capabilities`
- `quests`
- `resources`
- `facilitator_notes`

### Quest

A group of related tasks within a story arc.

Fields:

- `quest_id`
- `title`
- `narrative`
- `category`
- `difficulty`
- `base_points`
- `unlock_rule`
- `tasks`

### Task

A concrete objective that can be validated.

Fields:

- `task_id`
- `title`
- `objective`
- `instructions`
- `success_criteria`
- `points`
- `validators`
- `hints`
- `evidence_visible_to_player`
- `facilitator_notes`

### Validator

A machine-readable rule that decides whether an attempt passes.

Fields:

- `validator_id`
- `type`
- `mode`: `sync` or `async`
- `parameters`
- `expected_result`
- `timeout_seconds`
- `retry_policy`
- `safe_error_message`
- `evidence_policy`

### Attempt

A player's or team's submission for a task.

Fields:

- `attempt_id`
- `event_id`
- `team_id`
- `user_id`
- `task_id`
- `submission_payload`
- `status`
- `submitted_at`
- `completed_at`

### Validation Result

The normalized output of one validator.

Fields:

- `validation_result_id`
- `attempt_id`
- `validator_id`
- `status`: `passed`, `failed`, `error`, `skipped`
- `score_delta`
- `message`
- `evidence_json`
- `started_at`
- `completed_at`

### Scoring Event

Append-only points ledger.

Fields:

- `scoring_event_id`
- `event_id`
- `team_id`
- `user_id`
- `task_id`
- `source_type`
- `source_id`
- `points_delta`
- `reason`
- `created_at`

## Validator types

### 1. SQL assertion validator

Runs a safe SQL query and checks the result.

Use cases:

- table exists
- row count expected
- data quality rule met
- query returns expected value
- aggregation matches target output
- view/materialized view created
- governance table function works

Example:

```yaml
validators:
  - id: validate_customer_gold_table
    type: sql_assertion
    mode: sync
    warehouse: event_default
    statement: |
      SELECT COUNT(*) AS cnt
      FROM ${team_catalog}.${team_schema}.customer_gold
      WHERE customer_id IS NOT NULL
    expect:
      operator: ">="
      value: 1000
```

Safety rules:

- default read-only SQL
- block destructive statements unless explicitly allowlisted
- template variables are validated
- queries run under scoped service principal
- result evidence is truncated

### 2. Databricks SDK validator

Uses the Databricks SDK to inspect workspace objects.

Use cases:

- job exists
- job has schedule
- pipeline exists
- serving endpoint exists and is ready
- vector search index exists
- Unity Catalog grants are correct
- dashboard exists

Example:

```yaml
validators:
  - id: validate_job_schedule
    type: databricks_sdk
    check: job_exists_with_schedule
    mode: sync
    params:
      name_contains: "${team_slug}"
```

### 3. System-table checks (via `sql_assertion`)

System-table verification is plain `sql_assertion` against `system.*` tables —
a dedicated `system_table` type was removed because it had no executable
backend (the silent-skip failure mode the strict linter now blocks).

Use cases:

- query was run after event start (`system.query.history`)
- pipeline update completed (`system.lakeflow.pipeline_update_timeline`)
- team consumed a specific DBU product type (`system.billing.usage`)

Example:

```yaml
validators:
  - id: validate-pipeline-run
    type: sql_assertion
    mode: sync
    statement: |
      SELECT count(*) FROM system.lakeflow.pipeline_update_timeline
      WHERE result_state = 'COMPLETED'
        AND run_as_user_name IN (${team_members})
        AND period_start_time >= '${event_start}'
    expect:
      operator: ">="
      value: 1
```

> The `notebook` and `python_code` validator types were likewise removed from
> the vocabulary — every type that lints must execute. Their use cases (custom
> logic, artifact checks) are covered by `sql_assertion` evidence tables,
> `databricks_sdk` checks, `rest_api`, or `manual` host review.

### 4. REST/API validator (`rest_api`)

Queries a serving endpoint **by name** with a host-authored prompt and
evaluates the reply with the shared expectation engine. Handles chat
(`messages`) and agent/Responses (`input`) payloads.

Use cases:

- model endpoint invocation
- agent (KA/MAS) endpoint response check
- AI Gateway endpoint contract proof

### 5. Manual validator

A host can manually mark a task as passed, failed, or partially passed.

Use cases:

- subjective presentation tasks
- edge cases
- validation outage
- bonus challenge judging

## Validation status lifecycle

```text
submitted
  ↓
queued
  ↓
running
  ↓
passed | failed | error | timed_out | cancelled
```

## Scoring rules

Recommended scoring fields:

```yaml
scoring:
  base_points: 200
  first_blood_bonus: 50
  speed_bonus:
    enabled: true
    max_bonus: 100
    decay_after_minutes: 30
  hint_penalties:
    first_hint: -10
    second_hint: -25
    third_hint: -50
  max_attempts_without_penalty: 3
  attempt_penalty_after_max: -5
  partial_credit: true
```

## Idempotency

Each task should only award base points once per team unless explicitly repeatable.

Use idempotency key:

```text
event_id + team_id + task_id + scoring_rule_id
```

Manual overrides are separate scoring events and never mutate original events.

## Evidence model

Validators should produce evidence, but not all evidence should be exposed to players.

Recommended fields:

- `public_message`: safe player-facing result
- `private_message`: host-facing diagnostic
- `evidence_json`: structured evidence
- `raw_output_location`: optional secure storage pointer
- `redaction_status`

## Validator safety

Validation must not become an arbitrary code execution escape hatch.

Controls:

- validator type allowlist
- per-event capability allowlist
- read-only SQL default
- validator timeouts
- output truncation
- scoped service principal
- team resource namespace restrictions
- denylist for destructive statements
- host-only notebook validators
- audit all validation requests and results

## MVP validator set

Start with:

1. SQL assertion
2. Databricks SDK object check
3. Manual host validation

Then add:

4. System table validator
5. Notebook validator
6. Python code validator
7. REST/API validator

### Implemented in PR03

The first-class validation engine ships with **`sql_assertion`** and **`manual`**:

- Code lives in `app/validators/` (`base.py`, `safety.py`, `sql_assertion.py`,
  `manual.py`); dispatch + aggregation in `app/services/validation_engine.py`;
  the idempotent award in `app/services/scoring_service.py`
  (+ `app/repositories/scoring.py`).
- Submission endpoint: `POST /api/events/{event_id}/tasks/{task_id}/attempts`
  (see `docs/08`). It runs every enabled validator, persists one
  `validation_results` row each, transitions the `task_attempts` row, and — on a
  passing aggregate — writes a single `scoring_events` row.
- **Safety (read-only SQL):** `sql_assertion` allows exactly one `SELECT`/`WITH`
  statement; destructive/DDL/DML verbs and stacked statements are rejected.
  `${...}` template slots resolve **only** from server-provided variables
  (`team_catalog`, `team_schema`, `team_id`, `event_id`) — a player cannot
  introduce a slot or inject SQL through one. Evidence is row/char truncated.
- **Player-safe errors:** validators never raise to the player; a bad
  config/timeout/exec failure becomes an `error` outcome with a safe
  `public_message`, while the detailed `private_message` is kept host-side.
- **Idempotency:** base points are awarded once per scope — team in
  standalone/master, workspace in a federation child — enforced by the
  `scoring_events.idempotency_key` UNIQUE constraint.

Every type in the authoring vocabulary executes: `sql_assertion`,
`databricks_sdk`/`workspace_api`, `rest_api`, and `manual`. Unknown types are
lint **errors** (an unknown type would dispatch as `skipped` at runtime — the
stubbed-task failure mode the linter exists to block). See
[`AUTHORING_QUEST_PACKS.md`](AUTHORING_QUEST_PACKS.md) for the live contract.

## Authoring standard

Every quest task must include:

- objective
- success criteria
- one or more validators
- safe failure message
- facilitator note
- estimated time
- points
- hint ladder

If a task cannot be validated automatically, it must declare `manual_validation_required: true`.
