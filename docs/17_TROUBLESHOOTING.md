# 17 — Troubleshooting Guide

Practical fixes for the most common issues across **both modes** (Adoption and
Event/GameDay). Start with the health endpoint, then work down by symptom.

> **First stop:** `GET /api/health`. It returns `db_latency_ms`, the loaded
> `validator_types`, a `federation` block (`role`, `workspace_id`, `event_slug`),
> and per-subsystem `checks` for `lakebase`, `migrations`, `validators`,
> `scoring`, and `sql_warehouse`. Most problems below show up there first.

---

## Deployment

### `deploy.sh` fails at authentication
- Confirm the Databricks CLI is installed and `databricks auth describe` resolves
  a profile. Re-run `databricks auth login --host <workspace-url>` if needed.
- The script is non-interactive-friendly; pass `--profile <name>` to pick a
  specific profile.

### No SQL warehouse found / wrong warehouse
- The deploy selects a warehouse and writes `QUEST_SQL_WAREHOUSE_ID` into
  `app/app.yaml`. Verify the value matches a running warehouse
  (`databricks warehouses list`).
- Resource bootstrap/reset (PR08) and `sql_assertion` validators need a
  warehouse. Without one, `/api/health`'s `sql_warehouse` check reports
  `not_configured` and bootstrap returns `503 NO_WAREHOUSE`.

### Frontend changes not showing after deploy
- The React app is built into `app/static/` by `npm run build` (run from
  `frontend/`). The deploy rebuilds it; if you edited the frontend, rebuild and
  redeploy. Hard-refresh the browser (the JS bundle is content-hashed).

---

## Lakebase / database

### `/api/health` shows `lakebase: error` or high `db_latency_ms`
- Confirm the Lakebase instance is running and the app's identity has access.
- For **standalone/master**, the app uses workspace OAuth. For **child**, it uses
  the explicit writer credential (`LAKEBASE_*` env, see below) — a bad token
  surfaces here.

### `migrations` check is `pending` / tables missing
- Migrations run at startup, gated by `QUEST_EVENT_MODE`. If Event Mode is off,
  GameDay tables are intentionally absent — this is expected, not a bug.
- To (re-)run migrations manually: `python app/migrations/run_migrations.py`.
  Migrations are idempotent (`CREATE ... IF NOT EXISTS`), safe to run twice.

### "relation does not exist" errors
- The table name is wrong for the schema. Tasks live in `quest_tasks` (not
  `tasks`); admins in `quest_admins`. See `docs/07_DATA_MODEL.md`.

---

## Event Mode (GameDay)

### GameDay endpoints return 404
- Event Mode is **off**. This is the default. Enable with
  `./deploy.sh --event-mode` (or `QUEST_EVENT_MODE=on`), or deploy with
  `--role master|child` which implies it. Confirm via `/api/health` →
  `federation.role` and the presence of GameDay routes.

### "Submit" is disabled in the quest runner
- Submission is gated by `attempts_open && joined`. Two causes:
  1. **Event not started** — the host must move the event to `active`
     (Host console → lifecycle controls). `paused`/`frozen`/`completed` close
     attempts.
  2. **Player not on a team** — the player must join a team in the Lobby. A host
     can also assign members from the Host console.

### Admin/Host pages are not visible
- Admins are an allowlist. Seed initial admins at deploy with
  `--admins "alice@corp.com,bob@corp.com"` (sets `QUEST_ADMIN_ALLOWLIST`).
  Admins are stored in the shared `quest_admins` table so they apply across
  standalone/master/child; an existing admin can add more in-app
  (Admin panel). `is_admin` is returned by `GET /api/profile`.

### Validator always returns pending
- `manual` validators require host approval (Host console → attempts inspector).
  `databricks_sdk` validators are lint-valid but not auto-executable yet — pair
  them with a `manual` validator in the pack so the task is completable.

### `sql_assertion` fails unexpectedly
- The SQL safety layer (`services/safety.py`) allows a **single read-only**
  statement (`SELECT`/`WITH`). DDL/DML verbs are blocked. Template variables
  (`${team_catalog}`, `${team_schema}`) must resolve to safe identifiers.
- Check the attempt's evidence/diagnostics in the Host console for the actual
  query result vs. the expected value/operator.

### Resource bootstrap "refused" / out-of-namespace
- Reset refuses the **whole plan** if any target falls outside the event's
  namespace (reserved catalogs like `main`/`system`/`hive_metastore`, a bare
  catalog, a wildcard, or another event's schema). This is a safety feature —
  check each team's `team_catalog`/`team_schema` and the event's
  `config_json.resource_namespace`.

---

## Multi-workspace federation

### Child shows "not yet mapped"
- The child lab user isn't on the roster. The master host imports a roster CSV
  (`POST /api/host/events/{id}/roster/import`) mapping
  `workspace_id, lab_user_email, team_name`. Points earned before mapping are
  re-attributed on (idempotent) re-import.

### Child can't connect to the master Lakebase
- The child needs `LAKEBASE_HOST` = master's shared Lakebase host and
  `LAKEBASE_WRITER_TOKEN` (the shared event-writer credential), set via
  `--master-lakebase-host` / `--master-lakebase-token` at deploy. `/api/health`
  → `federation` + `lakebase` checks confirm connectivity.
- The writer role (default `quest_event_writer`) has restricted grants
  (INSERT on facts, SELECT/INSERT on `quest_admins`). It deliberately cannot run
  destructive statements.

### Duplicate points across workspaces
- Scoring is idempotent per `(workspace_id, event_id, source)` via a deterministic
  idempotency key. Re-running a child or re-submitting an attempt does not
  double-award. If you see duplicates, confirm `workspace_id` is being stamped
  (it appears in `scoring_events`).

---

## Reporting (PR11)

### Export returns `400 BAD_FORMAT`
- `format` must be `json`, `csv`, or `markdown`. Default is `json`.

### Report numbers look empty
- The report degrades gracefully — if Lakebase is partially unavailable, sections
  fall back to empty rather than 500ing. Check `/api/health` and that the event
  actually has teams, attempts, and scoring events.

---

## Adoption Mode

### Leaderboard/missions empty after deploy
- The scoring pipeline runs every 4 hours (or on demand). Trigger the scoring job
  manually, then confirm the Delta→Lakebase sync ran. See `SETUP.md`.
- Adoption Mode is unaffected by Event Mode — if GameDay is misbehaving, Adoption
  endpoints (`/api/profile`, `/api/missions`, `/api/leaderboard`) should still
  work independently.

---

## Getting more detail

- Every request carries an `X-Request-ID` (generated if absent). Errors return a
  standard envelope `{ "error": { "code", "message", "request_id" } }` — quote the
  `request_id` when reading logs.
- Validation and scoring emit structured key-value logs
  (`services/observability.py`). Grep app logs by `request_id`, `event_id`, or
  `task_id`.
