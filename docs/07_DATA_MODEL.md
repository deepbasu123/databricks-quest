# 07 — Data Model

## Data model principles

1. **Lakebase is operational state.** Events, attempts, validation results, and leaderboards need transactional low-latency reads/writes.
2. **Delta is analytics and audit.** Append-only facts should be synced to Delta for reporting, joins with system tables, and historical analysis.
3. **Scoring is append-only.** Do not mutate points without writing a compensating scoring event.
4. **Quest pack versions are immutable once an event starts.** This preserves fairness.
5. **Event state is scoped.** Every row must include `event_id` where applicable.

## Lakebase operational tables

### quest_packs

```sql
CREATE TABLE quest_packs (
  pack_id TEXT PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  description TEXT,
  owner TEXT,
  status TEXT NOT NULL DEFAULT 'draft',
  created_by TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### quest_pack_versions

```sql
CREATE TABLE quest_pack_versions (
  pack_version_id TEXT PRIMARY KEY,
  pack_id TEXT NOT NULL REFERENCES quest_packs(pack_id),
  version TEXT NOT NULL,
  manifest_json JSONB NOT NULL,
  content_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  imported_by TEXT,
  imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(pack_id, version)
);
```

### quests

```sql
CREATE TABLE quests (
  quest_id TEXT PRIMARY KEY,
  pack_version_id TEXT NOT NULL REFERENCES quest_pack_versions(pack_version_id),
  slug TEXT NOT NULL,
  title TEXT NOT NULL,
  narrative TEXT,
  category TEXT,
  difficulty TEXT,
  sort_order INT DEFAULT 0,
  base_points INT DEFAULT 0,
  unlock_rule_json JSONB,
  facilitator_notes TEXT,
  UNIQUE(pack_version_id, slug)
);
```

### quest_tasks

```sql
CREATE TABLE quest_tasks (
  task_id TEXT PRIMARY KEY,
  quest_id TEXT NOT NULL REFERENCES quests(quest_id),
  slug TEXT NOT NULL,
  title TEXT NOT NULL,
  objective TEXT NOT NULL,
  instructions_md TEXT,
  success_criteria_md TEXT,
  points INT NOT NULL DEFAULT 0,
  sort_order INT DEFAULT 0,
  validation_mode TEXT DEFAULT 'auto',
  scoring_json JSONB,
  metadata_json JSONB,
  UNIQUE(quest_id, slug)
);
```

### task_validators

```sql
CREATE TABLE task_validators (
  validator_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES quest_tasks(task_id),
  type TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'sync',
  config_json JSONB NOT NULL,
  expected_json JSONB,
  timeout_seconds INT DEFAULT 30,
  sort_order INT DEFAULT 0,
  enabled BOOLEAN DEFAULT TRUE
);
```

### task_hints

```sql
CREATE TABLE task_hints (
  hint_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES quest_tasks(task_id),
  sort_order INT NOT NULL,
  title TEXT,
  body_md TEXT NOT NULL,
  penalty_points INT DEFAULT 0
);
```

### events

```sql
CREATE TABLE events (
  event_id TEXT PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  description TEXT,
  pack_version_id TEXT NOT NULL REFERENCES quest_pack_versions(pack_version_id),
  mode TEXT NOT NULL DEFAULT 'gameday',
  status TEXT NOT NULL DEFAULT 'draft',
  starts_at TIMESTAMP,
  ends_at TIMESTAMP,
  timezone TEXT DEFAULT 'UTC',
  scoring_frozen_at TIMESTAMP,
  created_by TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  config_json JSONB
);
```

Event statuses:

```text
draft → ready → active → paused → frozen → completed → archived
```

### event_hosts

```sql
CREATE TABLE event_hosts (
  event_id TEXT NOT NULL REFERENCES events(event_id),
  user_id TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'host',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(event_id, user_id)
);
```

### teams

```sql
CREATE TABLE teams (
  team_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES events(event_id),
  name TEXT NOT NULL,
  display_name TEXT,
  color TEXT,
  team_catalog TEXT,
  team_schema TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(event_id, name)
);
```

### participants

```sql
CREATE TABLE participants (
  participant_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES events(event_id),
  user_id TEXT NOT NULL,
  display_name TEXT,
  email TEXT,
  role TEXT DEFAULT 'player',
  registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_seen_at TIMESTAMP,
  UNIQUE(event_id, user_id)
);
```

### team_members

```sql
CREATE TABLE team_members (
  team_id TEXT NOT NULL REFERENCES teams(team_id),
  participant_id TEXT NOT NULL REFERENCES participants(participant_id),
  joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(team_id, participant_id)
);
```

### task_attempts

```sql
CREATE TABLE task_attempts (
  attempt_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES events(event_id),
  team_id TEXT NOT NULL REFERENCES teams(team_id),
  task_id TEXT NOT NULL REFERENCES quest_tasks(task_id),
  submitted_by TEXT NOT NULL,
  submission_json JSONB,
  status TEXT NOT NULL DEFAULT 'submitted',
  submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  error_message TEXT
);
```

### validation_results

```sql
CREATE TABLE validation_results (
  validation_result_id TEXT PRIMARY KEY,
  attempt_id TEXT NOT NULL REFERENCES task_attempts(attempt_id),
  validator_id TEXT NOT NULL REFERENCES task_validators(validator_id),
  status TEXT NOT NULL,
  score_delta INT DEFAULT 0,
  public_message TEXT,
  private_message TEXT,
  evidence_json JSONB,
  started_at TIMESTAMP,
  completed_at TIMESTAMP
);
```

### scoring_events

```sql
CREATE TABLE scoring_events (
  scoring_event_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES events(event_id),
  team_id TEXT,
  user_id TEXT,
  quest_id TEXT,
  task_id TEXT,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  points_delta INT NOT NULL,
  reason TEXT NOT NULL,
  created_by TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  idempotency_key TEXT UNIQUE
);
```

### hints_taken

```sql
CREATE TABLE hints_taken (
  event_id TEXT NOT NULL REFERENCES events(event_id),
  team_id TEXT NOT NULL REFERENCES teams(team_id),
  task_id TEXT NOT NULL REFERENCES quest_tasks(task_id),
  hint_id TEXT NOT NULL REFERENCES task_hints(hint_id),
  taken_by TEXT NOT NULL,
  penalty_points INT DEFAULT 0,
  taken_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(event_id, team_id, hint_id)
);
```

### announcements

```sql
CREATE TABLE announcements (
  announcement_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES events(event_id),
  title TEXT NOT NULL,
  body_md TEXT NOT NULL,
  severity TEXT DEFAULT 'info',
  created_by TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### manual_adjustments

```sql
CREATE TABLE manual_adjustments (
  adjustment_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES events(event_id),
  team_id TEXT,
  user_id TEXT,
  task_id TEXT,
  points_delta INT NOT NULL,
  reason TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### event_audit_log

```sql
CREATE TABLE event_audit_log (
  audit_id TEXT PRIMARY KEY,
  event_id TEXT,
  actor_user_id TEXT,
  action TEXT NOT NULL,
  target_type TEXT,
  target_id TEXT,
  payload_json JSONB,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Derived views

### team_scores

```sql
CREATE VIEW team_scores AS
SELECT
  event_id,
  team_id,
  SUM(points_delta) AS total_points,
  COUNT(*) AS scoring_events,
  MAX(created_at) AS last_scored_at
FROM scoring_events
GROUP BY event_id, team_id;
```

### event_leaderboard

```sql
CREATE VIEW event_leaderboard AS
SELECT
  s.event_id,
  s.team_id,
  t.display_name,
  s.total_points,
  RANK() OVER (PARTITION BY s.event_id ORDER BY s.total_points DESC, s.last_scored_at ASC) AS rank,
  s.last_scored_at
FROM team_scores s
JOIN teams t ON s.team_id = t.team_id;
```

### task_completion_status

```sql
CREATE VIEW task_completion_status AS
SELECT
  a.event_id,
  a.team_id,
  a.task_id,
  MAX(CASE WHEN a.status = 'passed' THEN 1 ELSE 0 END) AS completed,
  COUNT(*) AS attempts,
  MAX(a.completed_at) AS last_attempt_at
FROM task_attempts a
GROUP BY a.event_id, a.team_id, a.task_id;
```

## Delta analytics tables

Sync these Lakebase tables to Delta as append-only facts or snapshots:

- `event_fact`
- `team_fact`
- `participant_fact`
- `task_attempt_fact`
- `validation_result_fact`
- `scoring_event_fact`
- `hint_usage_fact`
- `leaderboard_snapshot_fact`
- `event_audit_fact`

## Migration approach

PR1 should add migrations and a migration runner.

Recommended migration runner:

```bash
python app/migrations/run_migrations.py --lakebase-host ... --lakebase-db ...
```

For Databricks deploy:

- run migrations during `deploy.sh`
- run migrations idempotently before app startup
- record applied migrations in `schema_migrations`

### PR01 implementation status

Implemented in PR01:

- `app/db.py` — centralized Lakebase connection handling (shared by the app and
  the migration runner). Supports workspace-identity OAuth (app) and explicit
  credentials (deploy).
- `app/migrations/001_gameday_core.sql` — all operational tables above plus the
  `team_scores`, `event_leaderboard`, and `task_completion_status` views. Every
  statement is idempotent (`CREATE ... IF NOT EXISTS` / `CREATE OR REPLACE`).
- `app/migrations/run_migrations.py` — idempotent runner. Ensures
  `schema_migrations`, applies only unrecorded `*.sql` files (ordered by
  filename), and records each. Backends: psycopg2 (preferred), psql CLI
  fallback (no local psycopg2 needed), or workspace OAuth when no password is
  supplied. Safe to run repeatedly.
- `deploy.sh` runs the migrations against Lakebase right after provisioning,
  before the service-principal `GRANT`, so new tables are readable by the app.
- `/api/health` reports `migrations_applied` / `migrations_count`.

Repository/service stubs (`app/repositories/`, `app/services/audit.py`) expose
the read paths over these tables; mutation paths are deferred to later PRs.

### schema_migrations

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  description TEXT,
  applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```
