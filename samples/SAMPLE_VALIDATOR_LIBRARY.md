# Sample Validator Library

> **Start with [`docs/AUTHORING_QUEST_PACKS.md`](../docs/AUTHORING_QUEST_PACKS.md)** — the single guided authoring walkthrough. This file is the copy-paste validator-pattern appendix.
>
> Every pattern below **executes today**. Types without an executable backend
> were removed from the authoring vocabulary — a pack can no longer lint clean
> and silently skip at runtime.

## SQL assertion

```yaml
type: sql_assertion
mode: sync
statement: |
  SELECT COUNT(*) AS cnt
  FROM ${team_catalog}.${team_schema}.target_table
expect:
  operator: ">="
  value: 1
```

Supported operators:

```text
=, !=, >, >=, <, <=, contains, not_contains, is_true, is_false
```

System-table checks are plain `sql_assertion` statements too (the warehouse
must be able to read them):

```yaml
type: sql_assertion
mode: sync
statement: |
  SELECT COUNT(*) FROM system.query.history
  WHERE executed_by IN (${team_members})
    AND start_time >= '${event_start}'
    AND LOWER(statement_text) LIKE '%ai_query%'
expect:
  operator: ">="
  value: 1
```

## Databricks SDK — workspace artefact checks

Always pair with a `manual` validator and `manual_validation_required: true`
(the check degrades to host review when it can't run). Full check/param table:
[`docs/AUTHORING_QUEST_PACKS.md`](../docs/AUTHORING_QUEST_PACKS.md).

```yaml
type: databricks_sdk
mode: sync
check: job_exists_with_schedule
params:
  name_contains: "${team_slug}"
```

```yaml
type: databricks_sdk
mode: sync
check: pipeline_update_completed
params:
  name_contains: "${team_slug}"
```

```yaml
type: databricks_sdk
mode: sync
check: ai_gateway_configured
params:
  name: "${team_slug}-gateway"
  require_rate_limits: true
  require_usage_tracking: true
```

```yaml
type: databricks_sdk
mode: sync
check: genie_space_curated
params:
  name_contains: "${team_slug}"
  require_instructions: true
  min_sample_questions: 3
```

```yaml
type: databricks_sdk
mode: sync
check: knowledge_assistant_exists
params:
  name_contains: "${team_slug}"
```

## REST API — query a serving endpoint and assert on the answer

For "the team's model/agent answers correctly" tasks. Endpoints are addressed
by **serving-endpoint name only** (never a URL/headers — the linter rejects
those keys); the prompt is host-authored and the player's submission never
reaches the model. `max_tokens` is clamped to 512 and the timeout to 60s.

```yaml
type: rest_api
mode: sync
endpoint: "${team_slug}-ka-endpoint"
prompt: "What is our refund policy window, in days?"
max_tokens: 128
expect:
  operator: contains
  value: "30"
```

Pair with a `manual` fallback like the SDK checks — an unreachable endpoint
routes to host review, an evaluated-but-wrong answer fails.

## Manual validator

```yaml
type: manual
mode: async
rubric_md: |
  Award pass if the team can explain the architecture clearly and justify governance choices.
```
