# 08 — API Contract

## Principles

- Preserve existing adoption endpoints.
- Add versioned GameDay endpoints under `/api/events` and `/api/quest-packs`.
- Make write operations auditable.
- Return safe player-facing validation errors.
- Keep host-only endpoints separated.
- Use event and team scoping everywhere.

## Event Mode gating (opt-in)

GameDay is **off by default**. Every GameDay endpoint below — `/api/events/*`,
`/api/host/*`, and `/api/federation/*` — is gated on Event Mode
(`QUEST_EVENT_MODE` / `--event-mode`; implied by the `master`/`child` roles).
When Event Mode is **off**, these endpoints return:

```http
404 Not Found
{ "error": { "code": "EVENT_MODE_DISABLED",
             "message": "Event Mode is not enabled on this deployment." } }
```

The adoption endpoints (`/api/profile`, `/api/missions`, `/api/leaderboard`,
`/api/admin/*`, `/api/health`, `/api/notifications`) are always available.
`GET /api/health` reports `"event_mode": true|false` so clients can gate UI.

## Existing endpoints to preserve

```http
GET /api/health
GET /api/profile
GET /api/missions
GET /api/leaderboard?period=all|weekly|monthly
GET /api/notifications
GET /api/admin/stats
GET /api/admin/pipeline-status
```

### `/api/health` migration status (PR01)

`/api/health` keeps its existing fields (`status`, `db_connected`, `timestamp`)
and additionally reports GameDay migration status. The new fields are additive
and safe for existing clients to ignore:

```json
{
  "status": "ok",
  "db_connected": true,
  "migrations_applied": ["001_gameday_core"],
  "migrations_count": 1,
  "timestamp": "2026-06-03T00:00:00.000000"
}
```

`migrations_applied` is an empty list when Lakebase is unavailable or migrations
have not yet run, so health never fails because of migration state.

## New public/player endpoints

### List active events

```http
GET /api/events
```

Response:

```json
{
  "events": [
    {
      "event_id": "evt_123",
      "title": "AI/BI GameDay",
      "status": "active",
      "starts_at": "2026-06-10T09:00:00Z",
      "ends_at": "2026-06-10T12:00:00Z",
      "pack_title": "AI/BI Intelligence Challenge"
    }
  ]
}
```

### Get event lobby

```http
GET /api/events/{event_id}
```

Response includes:

- event summary
- current user registration status
- team assignment
- event phase
- countdown metadata
- latest announcements

### Join event

```http
POST /api/events/{event_id}/join
```

Request:

```json
{
  "team_code": "optional-team-code",
  "display_name": "Alistair"
}
```

### Get team dashboard

```http
GET /api/events/{event_id}/team
```

Returns:

- team details
- members
- score
- rank
- quest progress
- recent validations
- available hints

### List event quests

```http
GET /api/events/{event_id}/quests
```

Returns event-scoped quests with completion status.

### Get quest detail

```http
GET /api/events/{event_id}/quests/{quest_id}
```

Returns:

- narrative
- tasks
- instructions
- hints available
- completion status
- submission schema

### Submit task attempt

```http
POST /api/events/{event_id}/tasks/{task_id}/attempts
```

Request:

```json
{
  "submission": {
    "catalog": "team_01",
    "schema": "silver",
    "object_name": "customer_gold"
  }
}
```

Response for sync validation (implemented in PR03):

```json
{
  "attempt_id": "att_123",
  "status": "passed",
  "message": "Task complete! +200 points.",
  "points_awarded": 200,
  "already_awarded": false,
  "results": [
    { "status": "passed", "message": "Validated successfully." }
  ],
  "team_id": "team_red"
}
```

`status` is one of `passed | failed | manual | error` (the MVP runs SQL
synchronously). `results` carries one **player-safe** message per validator; raw
validator diagnostics are persisted to `validation_results.private_message` for
the host and never returned here. A repeat passing submission returns
`status: "passed"` with `already_awarded: true` and `points_awarded: 0` — base
points are awarded once per scope (team in standalone/master, workspace in a
federation child), enforced by the `scoring_events.idempotency_key` UNIQUE
constraint. `manual` validators return `status: "manual"` (pending host review,
no points). Identity/team resolution: standalone/master resolve the team via
team membership; a federation child resolves it via the identity map and stamps
`workspace_id` so the master attributes the score after roster import.

Response for async validation (future — async validator execution):

```json
{
  "attempt_id": "att_123",
  "status": "queued",
  "message": "Validation queued. This can take up to 60 seconds."
}
```

### Get attempt status

```http
GET /api/events/{event_id}/attempts/{attempt_id}
```

Response (implemented in PR03):

```json
{
  "attempt": {
    "attempt_id": "att_123",
    "task_id": "tsk_1",
    "team_id": "team_red",
    "status": "passed",
    "submitted_at": "2026-06-03T05:00:00",
    "completed_at": "2026-06-03T05:00:01"
  },
  "results": [
    { "validation_result_id": "vres_1", "validator_id": "val_1",
      "status": "passed", "score_delta": 0,
      "public_message": "Validated successfully." }
  ]
}
```

Results expose only player-safe fields (no `private_message`).

### Request hint

```http
POST /api/events/{event_id}/tasks/{task_id}/hints/{hint_id}/take
```

Response:

```json
{
  "hint_id": "hint_1",
  "body_md": "Check the table owner and grants in Catalog Explorer.",
  "penalty_points": -10,
  "team_score": 1240
}
```

### Get event leaderboard

```http
GET /api/events/{event_id}/leaderboard
```

Optional query params:

- `scope=team|individual`
- `quest_id=...`
- `include_recent=true`

## Host/admin endpoints

### Import quest pack

```http
POST /api/host/quest-packs/import
```

Request:

```json
{
  "manifest_yaml": "..."
}
```

Response:

```json
{
  "pack_id": "pack_ai_bi",
  "pack_version_id": "packver_123",
  "status": "imported",
  "warnings": []
}
```

### Lint quest pack

```http
POST /api/host/quest-packs/lint
```

### List quest packs

```http
GET /api/host/quest-packs
```

### Get quest pack

```http
GET /api/host/quest-packs/{pack_id}
```

### Event & team lifecycle — PR04 implementation status

Implemented in PR04. All are Event-Mode-gated (404 when Event Mode is off);
host endpoints additionally enforce `QUEST_HOST_ALLOWLIST` via `require_host`.

Player endpoints:

- `GET /api/events` → `{ events: [{ event_id, slug, title, status, team_count, … }] }`
  — events visible to players (`ready`/`active`/`paused`/`frozen`).
- `GET /api/events/{event_id}` (id or slug) → lobby:
  `{ event, joinable, attempts_open, is_host, teams[], counts, you }`.
- `POST /api/events/{event_id}/join` body `{ display_name?, team_id?, team_name? }`
  → `{ joined, participant_id, team_id, team_name }`. Idempotent; `409 NOT_JOINABLE`
  unless the event is `ready`/`active`/`paused`.

Host endpoints:

- `POST /api/host/events` body `{ title, pack_version_id, slug?, description?,
  timezone? }` → `{ event }`. Creator is recorded as an `owner` host. `400` for an
  unknown `pack_version_id`; `409 SLUG_EXISTS` for a duplicate slug.
- `POST /api/host/events/{event_id}/teams` body `{ name, display_name?, color?,
  team_catalog?, team_schema? }` → `{ team }`. `409 TEAM_EXISTS` on duplicate name.
- `POST /api/host/events/{event_id}/participants/import` body
  `{ participants: [{ email|user_id, display_name?, team_name? }] }` →
  `{ rows, participants_created, teams_created, assignments }`. Idempotent; teams
  named in rows are created on demand.
- Lifecycle: `POST /api/host/events/{event_id}/{start|pause|freeze|complete|ready|archive}`
  → `{ event }`. Enforces the state machine; `409 INVALID_TRANSITION` on an
  illegal move.

**State machine.** `draft → ready|active|archived`; `ready → active|archived`;
`active → paused|frozen|completed`; `paused → active|frozen|completed`;
`frozen → active|completed`; `completed → archived`. `start`→`active` stamps
`starts_at`; `complete` stamps `ends_at`; `freeze` stamps `scoring_frozen_at`;
unfreeze (`frozen→active`) clears it.

**Attempt gating.** `POST /api/events/{event_id}/tasks/{task_id}/attempts` only
accepts submissions while the event is `active`; any other status returns
`409 EVENT_NOT_ACTIVE` with a player-safe message. Every mutation above writes an
`event_audit_log` row.

### Quest pack endpoints — PR02 implementation status

Implemented in PR02 (lint, import, list, get):

- Request body for lint/import is `{ "manifest_yaml": "<yaml string>" }`.
- **Lint** always returns `200` with `{ ok, errors[], warnings[], summary }`.
  `summary` has `quests`/`tasks`/`validators`/`hints` counts. `errors` and
  `warnings` are `{ loc, message }`.
- **Import** lints first and refuses on errors (`400` with the error model plus
  a `lint` block). On success returns
  `{ pack_id, pack_version_id, status, counts, content_hash, warnings }`.
  `status` is `imported` for a new version or `duplicate` when identical content
  for the same `(slug, version)` was already imported (idempotent).
- **Immutability:** re-importing an existing `(slug, version)` with *different*
  content returns `400` (`QUEST_PACK_INVALID`) — bump `pack.version` instead.
- **Auth:** `/api/host/*` resolve the user from forwarded headers. An optional
  `QUEST_HOST_ALLOWLIST` env (comma-separated emails) restricts access; when
  unset the endpoints are open (parity with `/api/admin`). Full event-role
  enforcement is a later PR.

Example (against the running app, in an authenticated browser session or with
forwarded-identity headers):

```bash
# Lint the built-in sample pack
curl -sX POST "$APP_URL/api/host/quest-packs/lint" \
  -H 'Content-Type: application/json' \
  --data "$(python3 -c 'import json,sys; print(json.dumps({"manifest_yaml": open("quest_packs/built_in/ai_bi_gameday.yml").read()}))')"

# Import it
curl -sX POST "$APP_URL/api/host/quest-packs/import" \
  -H 'Content-Type: application/json' \
  --data "$(python3 -c 'import json,sys; print(json.dumps({"manifest_yaml": open("quest_packs/built_in/ai_bi_gameday.yml").read()}))')"

# List, then fetch detail
curl -s "$APP_URL/api/host/quest-packs"
curl -s "$APP_URL/api/host/quest-packs/<pack_id>"
```

Local linting without a server: `python scripts/lint_quest_pack.py [path]`.

### Create event

```http
POST /api/host/events
```

Request:

```json
{
  "title": "Customer AI/BI GameDay",
  "slug": "customer-ai-bi-gameday",
  "pack_version_id": "packver_123",
  "starts_at": "2026-06-10T09:00:00Z",
  "ends_at": "2026-06-10T12:00:00Z",
  "config": {
    "team_mode": true,
    "max_team_size": 4,
    "allow_self_team_select": false
  }
}
```

### Import participants

```http
POST /api/host/events/{event_id}/participants/import
```

Request:

```json
{
  "participants": [
    {"email": "user@example.com", "display_name": "User Name", "team": "Team 1"}
  ]
}
```

### Create teams

```http
POST /api/host/events/{event_id}/teams
```

### Start/pause/freeze/end event

```http
POST /api/host/events/{event_id}/start
POST /api/host/events/{event_id}/pause
POST /api/host/events/{event_id}/freeze
POST /api/host/events/{event_id}/complete
```

### Dry-run event validators

```http
POST /api/host/events/{event_id}/dry-run
```

### Manual score adjustment

```http
POST /api/host/events/{event_id}/score-adjustments
```

Request:

```json
{
  "team_id": "team_1",
  "task_id": "task_optional",
  "points_delta": 50,
  "reason": "Bonus for best explanation during debrief."
}
```

### Review validation attempts

```http
GET /api/host/events/{event_id}/attempts?status=failed
```

### Create announcement

```http
POST /api/host/events/{event_id}/announcements
```

### Reset event resources

```http
POST /api/host/events/{event_id}/reset
```

### Export event report

```http
GET /api/host/events/{event_id}/export
```

Return formats:

- JSON
- CSV
- Markdown

## Multi-workspace federation endpoints (ADR_006)

These endpoints support the shared-Lakebase multi-workspace mode. They are
additive — a standalone deploy still exposes them, but `/api/federation/status`
reports `role: "standalone"` and the host endpoints are unused. See
`adr/ADR_006_SHARED_LAKEBASE_MULTI_WORKSPACE_FEDERATION.md`.

`/api/health` additionally returns a `federation` block (`role`, `workspace_id`,
`event_slug`) so operators can confirm a deploy's role at a glance. The fields
are additive and safe for existing clients to ignore.

### Federation status (any role)

```http
GET /api/federation/status
```

Returns this deploy's role and — for a child — whether the current lab user is
mapped to a team yet, plus a DB-connection health flag for the UI indicator:

```json
{
  "role": "child",
  "workspace_id": "ws-anzgt-01",
  "event_slug": "anz-gameday",
  "event_id": "evt_123",
  "submitted_by": "labuser+1@awsbricks.com",
  "mapped": true,
  "db_connected": true,
  "team": { "team_id": "team_1", "display_name": "Red Team" }
}
```

### Event-wide leaderboard (child read)

```http
GET /api/federation/leaderboard?event={optional slug or id}
```

Reads the shared `event_leaderboard` view (spans every workspace) and highlights
this workspace's own team:

```json
{
  "leaderboard": [
    { "event_id": "evt_123", "team_id": "team_1", "display_name": "Red Team",
      "total_points": 1250, "rank": 1, "last_scored_at": "2026-06-10T10:05:00Z" }
  ],
  "you": { "team_id": "team_1", "display_name": "Red Team", "total_points": 1250, "rank": 1 },
  "mapped": true,
  "event_id": "evt_123",
  "workspace_id": "ws-anzgt-01"
}
```

When this workspace's lab user is not yet on the roster, `mapped` is `false` and
`you` is `null` — the child UI shows a graceful "not yet mapped" state while the
player keeps earning (unattributed) points.

### Import roster (master host)

```http
POST /api/host/events/{event_id}/roster/import
```

Request — a CSV mapping each lab workspace/user to a real person and team.
Columns: `workspace_id` (or `workspace_host`), `lab_user_email`, `team_name`,
optional `display_name`, `real_email`:

```json
{ "csv": "workspace_id,lab_user_email,display_name,real_email,team_name\nws-anzgt-01,labuser+1@awsbricks.com,Ada Lovelace,ada@corp.com,Red Team" }
```

Response (idempotent — re-import is safe and re-attributes previously unmapped
scores):

```json
{
  "event_id": "evt_123",
  "rows": 24,
  "teams_created": 6,
  "participants_created": 24,
  "identities_mapped": 24,
  "status": "imported"
}
```

### Workspace health (master host)

```http
GET /api/host/events/{event_id}/workspaces
```

Returns one row per checked-in child with write counts and last-seen time for
the host console health panel.

### Unmapped identities (master host)

```http
GET /api/host/events/{event_id}/identities/unmapped
```

Returns federated `(workspace_id, lab_user_email)` pairs writing scores that are
not on the roster yet, with their unattributed point totals — the host
reconciliation worklist.

## Error model

All API errors should return:

```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "Player-safe error message.",
    "request_id": "req_123"
  }
}
```

Host endpoints may include additional diagnostic detail.

## Auth and roles

Resolve user identity from Databricks App forwarded headers as the current app does, then map to event role.

Roles:

- `player`
- `team_captain`
- `host`
- `admin`

Permission examples:

| Action | Player | Captain | Host | Admin |
|---|---:|---:|---:|---:|
| View event | yes | yes | yes | yes |
| Join team | yes | yes | yes | yes |
| Submit attempt | yes | yes | yes | yes |
| Take hint | yes | yes | yes | yes |
| Create event | no | no | yes | yes |
| Import quest pack | no | no | yes | yes |
| Start/freeze event | no | no | yes | yes |
| Manual adjust score | no | no | yes | yes |
| Reset resources | no | no | yes | yes |

## Compatibility note

Do not break existing frontend while adding these APIs. Add the new APIs and route new pages gradually.
