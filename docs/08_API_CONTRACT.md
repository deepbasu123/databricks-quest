# 08 — API Contract

## Principles

- Preserve existing adoption endpoints.
- Add versioned GameDay endpoints under `/api/events` and `/api/quest-packs`.
- Make write operations auditable.
- Return safe player-facing validation errors.
- Keep host-only endpoints separated.
- Use event and team scoping everywhere.

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

Response for sync validation:

```json
{
  "attempt_id": "att_123",
  "status": "passed",
  "message": "Validated customer_gold table with 10,000 valid rows.",
  "points_awarded": 200,
  "team_score": 1250
}
```

Response for async validation:

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
