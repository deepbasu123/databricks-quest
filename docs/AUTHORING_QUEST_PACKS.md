# Authoring Quest Packs — the single guided walkthrough

This is the **canonical** guide for building and updating Databricks Quest
GameDay quest packs. It stitches together the five deep-dive references into one
linear path: **scaffold → author → lint → import → version-bump**.

> **Agents:** the `.cursor/skills/quest-pack-author/` skill drives this workflow.
> It encodes the rules below so a pack is right the first time. This document is
> the skill's reference; humans can follow it directly too.

Deep-dive appendices (each starts with a pointer back here):

- [`samples/QUEST_PACK_SCHEMA.md`](../samples/QUEST_PACK_SCHEMA.md) — full field-by-field schema.
- [`samples/SAMPLE_VALIDATOR_LIBRARY.md`](../samples/SAMPLE_VALIDATOR_LIBRARY.md) — copy-paste validator patterns.
- [`samples/SAMPLE_QUEST_PACK_AI_BI.md`](../samples/SAMPLE_QUEST_PACK_AI_BI.md) — a complete worked pack.
- [`samples/packs/README.md`](../samples/packs/README.md) — run-flow / customization.
- [`docs/06_QUEST_MODEL_AND_VALIDATION_ENGINE.md`](06_QUEST_MODEL_AND_VALIDATION_ENGINE.md) — the model + validation engine.

---

## What a quest pack is

A quest pack is a **YAML manifest** — configuration, not code. It declares:

```
pack            → slug, title, version, metadata
scenario        → the narrative framing
quests[]        → ordered, optionally unlock-gated
  tasks[]       → the scored units
    validators[] → how a task is proven (sql_assertion / databricks_sdk / manual)
    hints[]      → optional, penalty-bearing
resources       → per-team namespace + optional seed SQL
```

The platform imports the manifest into Lakebase. A host then creates an **event**
from an imported pack version, adds teams, bootstraps each team's
`catalog.schema`, and starts play. Authoring stops at **import**; events and
bootstrap are the host's job.

---

## 1. Scaffold

Start from a known-good skeleton (one `sql_assertion` task that passes before any
resources exist, one `manual` task):

```bash
python scripts/new_quest_pack.py --slug my-gameday --title "My GameDay"
# → quest_packs/built_in/my-gameday.yml
```

Then edit the `TODO` markers. (You can also copy
[`quest_packs/built_in/ai_bi_gameday.yml`](../quest_packs/built_in/ai_bi_gameday.yml)
as a richer starting point.)

---

## 2. Author — the rules the linter enforces

Get these right and the linter passes first try. They are enforced by
[`app/services/quest_pack_linter.py`](../app/services/quest_pack_linter.py) and
the Pydantic models in [`app/models/quest_pack.py`](../app/models/quest_pack.py).

### Required fields

- **Top level:** `schema_version: "1.0"` (only `1.0` is supported), `pack`, `quests` (≥1).
- **`pack`:** `slug`, `title`, `version`.
- **Quest:** `slug`, `title`, and ≥1 `task`.
- **Task:** `slug`, `title`, `objective`. Plus **either** ≥1 validator **or** `manual_validation_required: true`.
- **Validator:** `id`, `type`. `mode` defaults to `sync` (must be `sync` or `async`).

### Slugs

- Lowercase letters, digits, single dashes: `^[a-z0-9]+(?:-[a-z0-9]+)*$` (e.g. `q1-foundation`).
- Quest slugs unique within the pack; task slugs unique within their quest; validator `id`s unique within their task.

### Points & hints

- `task.points >= 0`.
- Hint `penalty_points` should be **`<= 0`** (a penalty). A positive value lints as a warning. The server normalises to a non-positive delta and charges **once per team** (re-revealing is free).

### Unlock gating

- `unlock_rule.type: always` (default) or `quest_completed`.
- `quest_completed` requires `quest_slug`, it must reference a **real, earlier** quest, and **a quest cannot gate on itself**.

### Validator types

| Type | Executes? | Required config |
|---|---|---|
| `sql_assertion` | ✅ yes | `statement` (read-only `SELECT`/`WITH`), usually an `expect` block |
| `databricks_sdk` / `workspace_api` | ✅ yes | `check` (one of the registry names below) + its required params |
| `rest_api` | ✅ yes | `endpoint` (serving-endpoint **name**, never a URL) + `prompt`; usually an `expect` block |
| `manual` | host review | — |

**Every type in the vocabulary executes** — `system_table`, `notebook`, and
`python_code` were removed because they had no backend (a pack could lint clean
and silently skip at runtime). System-table use cases are plain `sql_assertion`
statements against `system.*` tables. The linter now **errors** on any type not
listed above. Confirm the live set at runtime: `GET /api/health` returns
`validator_types` and `sdk_checks`.

`rest_api` safety: config keys `url`/`headers`/`auth`/`token` are lint errors —
the only egress is the workspace API host via the app's own identity; prompts
are host-authored; `max_tokens` ≤ 512 and timeout ≤ 60s are clamped server-side.

### `resources.workspace` — non-SQL resources the bootstrap provisions

Beyond `seed_sql`, a pack may request workspace resources the host bootstrap
creates (and event teardown deletes):

```yaml
resources:
  workspace:
    - type: serving_endpoint        # lakebase_instance | vector_search_endpoint
      name: "quest-${event_slug}-${team_slug}-gw"   # | serving_endpoint | genie_space
      spec: {config: {served_entities: [...]}}
    - type: lakebase_instance
      name: "quest-${event_slug}-pg"
      spec: {capacity: CU_1}
```

Rules (lint-enforced): the `type` must be one of the four known kinds; `name`
must include `${event_slug}` — rendered names must start with the event's
namespace prefix `quest-<slug>-` (any separator) or the plan flags a blocker.
A `${team_slug}` slot makes the entry per-team. Cost caps: 1 Lakebase
instance, 1 vector search endpoint, 32 serving endpoints per event — exceeding
a cap is a plan blocker, never silent truncation. Teardown deletes only
resources that are **registry-recorded for the event AND namespace-prefixed**.

### `solutions` — the machine-playability contract (host-only)

Every task with auto validators carries ordered `solutions:` steps that perform
the task's intended action. The operator preflight harness executes them to
prove the pack is winnable end-to-end before an event; players never see them
(they live only in the host-side manifest, not in player task rows). Steps:

```yaml
solutions:
  - sql: "CREATE OR REPLACE TABLE ${team_catalog}.${team_schema}.silver AS SELECT ..."
  - workspace_op:           # executed by the preflight's workspace executor
      op: publish_dashboard # e.g. publish_dashboard, create_genie_space,
      name: "${team_slug} revenue"   # create_pipeline_and_run, create_scheduled_job
      dataset_sql: "SELECT ..."
  - skip: "seeded by host bootstrap"   # nothing to do; reason required
```

Each step is **exactly one of** `sql` / `workspace_op` (mapping with an `op`
name) / `skip` (reason string). Tasks whose only validators are `manual` are
exempt.

### Strict mode — the shipped-pack playability gate

`python scripts/lint_quest_pack.py --strict <pack>` (or `"strict": true` on the
lint API) adds the rules CI enforces on every shipped pack:

- every task with auto validators has `solutions`;
- every `databricks_sdk`/`workspace_api`/`rest_api` validator pairs with a
  `manual` validator **and** `manual_validation_required: true`;
- manual-only tasks set `manual_validation_required: true`;
- an unknown `databricks_sdk` check name is an **error** (warning otherwise).

`tests/test_sample_packs.py` strict-lints everything in `quest_packs/built_in/`
and `samples/packs/` — a pack that can't prove playability can't ship.

After strict lint, verify the SQL surface live before opening a PR:

```bash
python scripts/verify_pack_sql.py quest_packs/built_in/<pack>.yml \
  --warehouse-id <id> --catalog <writable_catalog>
```

It seeds a scratch schema, executes every `sql` solution step, evaluates every
`sql_assertion`, and drops the schema — catching SQL syntax/reconciliation bugs
that static lint cannot. Validators that depend on `workspace_op` outputs are
reported as SKIP; the full event preflight covers those.

### `sql_assertion` safety (critical)

The SQL safety layer ([`app/validators/safety.py`](../app/validators/safety.py))
is intentionally narrow:

- **Only** read-only `SELECT` / `WITH … SELECT`. No `SHOW`/`DESCRIBE`/`EXPLAIN`, no DDL/DML, no stacked statements.
- `expect.operator` ∈ `=, !=, >, >=, <, <=, contains, not_contains, is_true, is_false`.
- Template variables are **server-resolved** from a fixed allowlist. Any other `${…}` slot is **rejected** by the safety layer (and warned by the linter):

  `event_id, event_slug, team_id, team_slug, team_prefix, team_catalog, team_schema, event_catalog, event_schema, event_start, event_end, current_user, team_members`

  Scope every query to the team namespace: `FROM ${team_catalog}.${team_schema}.<table>`.

### `databricks_sdk` checks (read-only) + the **mandatory manual fallback**

Registry names (from [`app/services/sdk_checks.py`](../app/services/sdk_checks.py)):

| Check | Required params | Optional params |
|---|---|---|
| `dashboard_exists_for_team` | — | `name_contains` |
| `dashboard_published` | — | `name_contains` |
| `genie_space_exists` | — | `name_contains` |
| `table_exists` | `table` | — |
| `job_exists_with_schedule` | — | `name_contains` |
| `pipeline_update_completed` | — | `name_contains` |
| `serving_endpoint_exists` | `name` or `name_contains` | `require_ready` (default true), `task_contains` |
| `ai_gateway_configured` | `name` or `name_contains` | `require_rate_limits`, `require_guardrails`, `require_usage_tracking`, `require_inference_table`, `require_fallbacks` |
| `lakebase_instance_exists` | `name` or `name_contains` | `require_available` (default true) |
| `lakebase_synced_table_online` | `table` (3-part UC name) | — |
| `vector_search_endpoint_exists` | `name` or `name_contains` | `require_online` (default true) |
| `vector_search_index_ready` | `index` (3-part name) | `min_rows` |
| `genie_space_curated` | `space_id` or `name_contains` | `require_instructions`, `min_sample_questions`, `min_tables` |
| `genie_conversation_started` | `space_id` or `name_contains` | `min_conversations` (default 1), `created_after` (e.g. `${event_start}`) |
| `lakebase_app_connected` | `name` or `name_contains` | `require_running` (default true) |
| `knowledge_assistant_exists` | `name` or `name_contains` | `require_endpoint` (default true) |
| `multi_agent_supervisor_exists` | `name` or `name_contains` | `require_endpoint` (default true) |

> **Agent Bricks note:** `knowledge_assistant_exists` / `multi_agent_supervisor_exists`
> use the workspace's beta tiles REST surface (`/api/2.0/tiles`) — there is no
> public SDK API for KA/MAS yet. The surface was verified live; if it moves, the
> checks degrade to host review like any other runtime failure.
> **Genie conversation visibility:** `genie_conversation_started` lists
> conversations with `include_all=true`; the app's identity needs visibility into
> the space (CAN MANAGE, or being in the team's group). If it can't see them, the
> check degrades to host review.

The linter enforces these contracts: a known check missing a required param is a
lint **error**; an unknown check name or unused param is a warning. `${…}` slots
in params resolve from the same server-provided variables as `sql_assertion`.

> **Rule: pair every `databricks_sdk` task with a `manual` validator and set
> `manual_validation_required: true`.** SDK checks degrade to host review if the
> workspace client is unavailable or a check can't run — the manual fallback
> guarantees a pilot is never blocked. Example:

```yaml
- slug: ship-dashboard
  title: Ship the executive dashboard
  objective: Publish an AI/BI dashboard for your team.
  points: 200
  manual_validation_required: true
  validators:
    - id: dash-exists
      type: databricks_sdk
      mode: sync
      check: dashboard_exists_for_team
      params:
        name_contains: "${team_slug}"
    - id: host-confirm
      type: manual
      mode: sync
  hints:
    - title: Where to build
      penalty_points: -10
      body_md: Use Dashboards → Create, then publish.
```

---

## 3. Lint — iterate until clean

Lint locally (no server, no database):

```bash
python scripts/lint_quest_pack.py quest_packs/built_in/my-gameday.yml
```

`errors` block import; `warnings` are advisory (unknown template var, unknown
validator type, positive hint penalty, missing `expect`). Fix all errors and
review every warning. The CLI prints the **content hash** — the value the import
path uses for idempotency (see versioning below).

Against a running app (host-gated — see workstream A in
[`README_GAMEDAY.md`](../README_GAMEDAY.md)):

```bash
curl -sX POST "$APP_URL/api/host/quest-packs/lint" \
  -H 'Content-Type: application/json' \
  --data "$(python3 -c 'import json,sys; print(json.dumps({"manifest_yaml": open(sys.argv[1]).read()}))' quest_packs/built_in/my-gameday.yml)"
```

---

## 4. Import

```bash
curl -sX POST "$APP_URL/api/host/quest-packs/import" \
  -H 'Content-Type: application/json' \
  --data "$(python3 -c 'import json,sys; print(json.dumps({"manifest_yaml": open(sys.argv[1]).read()}))' quest_packs/built_in/my-gameday.yml)"

# Confirm
curl -s "$APP_URL/api/host/quest-packs"
```

Import returns the created `pack_version_id` (the value a host passes to
`POST /api/host/events`).

---

## 5. Versioning — immutable, so bump to change

**`(slug, version)` is immutable.** Re-importing the **same** version with the
**same** content is a no-op (`duplicate`). Re-importing the same version with
**changed** content is **rejected** (`ImmutableVersionError`). So the update flow
is:

1. Read the current YAML.
2. Apply your edits.
3. **Bump `pack.version`** (e.g. `0.1.0` → `0.1.1`). Use semver-ish strings.
4. Re-lint, then re-import. The new version imports alongside the old; events
   pin to a specific version, so existing events are unaffected.

---

## End-to-end (copy/paste)

```bash
# 1. Scaffold
python scripts/new_quest_pack.py --slug demo-pack --title "Demo Pack"
# 2. Author: edit quest_packs/built_in/demo-pack.yml
# 3. Lint until clean
python scripts/lint_quest_pack.py quest_packs/built_in/demo-pack.yml
# 4. Import (host session)
curl -sX POST "$APP_URL/api/host/quest-packs/import" -H 'Content-Type: application/json' \
  --data "$(python3 -c 'import json,sys; print(json.dumps({"manifest_yaml": open(sys.argv[1]).read()}))' quest_packs/built_in/demo-pack.yml)"
# 5. To change it later: edit, bump pack.version, re-lint, re-import.
```
