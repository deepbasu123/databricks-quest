# Databricks Quest — GameDay Mode

> **Living document.** GameDay (Event Mode) is being built PR by PR. This README
> tracks how to deploy and operate it **as features land**, and marks what works
> today vs. what's coming. The adoption-mode product is documented in
> [`README.md`](README.md); the deep design lives in [`docs/`](docs/).

Databricks Quest runs in two modes from **one codebase and one build**:

- **Adoption Mode** (default) — the system-table-driven platform-adoption game.
  Unchanged; see [`README.md`](README.md).
- **Event Mode (GameDay)** — configurable, quest-pack-driven, team-based events
  with validation, scoring, leaderboards, host controls, and (optionally)
  many workspaces federating into one.

Nothing here breaks Adoption Mode. **Event Mode is opt-in and off by default** —
a deploy with no GameDay flags is the legacy adoption app, byte-for-byte: the
GameDay APIs return `404`, the Event UI is hidden, and the GameDay schema
migrations are skipped. Enable GameDay explicitly with `--event-mode` (or set
`QUEST_EVENT_MODE=on`); the `master`/`child` roles imply it.

---

## Build status

**Live status lives in one place:
[`docs/STATUS.md`](docs/STATUS.md)** — the authoritative per-PR tracker (what's
landed, what's deployable/testable, known gaps). In short: PR01, PR02, and the
federation plumbing (PR13–PR16) have landed; the gameplay write path (PR03) and
event/team APIs (PR04) are still to come.

> **What this means for testing:** the deploy/migration/quest-pack/federation
> *plumbing* is testable now. **End-to-end gameplay** (play → validate → score →
> leaderboard) needs **PR03 + PR04**, because the attempt-submission and
> event-creation endpoints don't exist yet. See [Testing](#testing).

---

## Deployment switches

Everything is selected by runtime parameters (env vars set from `deploy.sh`
flags). There are **two orthogonal switches**:

1. **`QUEST_EVENT_MODE`** (`--event-mode`) — the GameDay master switch, **off by
   default**. Off = legacy adoption app (GameDay APIs `404`, Event UI hidden,
   GameDay migrations skipped). On = GameDay surfaces activate. Implied by the
   `master`/`child` roles.
2. **`QUEST_ROLE`** — federation topology, only meaningful once Event Mode is on:

| Role | What it is | Lakebase | GameDay migrations | Scoring pipeline |
|---|---|---|---|---|
| `standalone` (default) | Single-workspace app | provisions its own local | only if `--event-mode` | runs (adoption) |
| `master` | Owns the shared event DB + host console (implies Event Mode) | provisions its own | runs (incl. `002`) | runs (adoption) |
| `child` | One per attendee; writes to the master's DB (implies Event Mode) | **points at master** | **skipped** | **skipped** |

See [`adr/ADR_006_SHARED_LAKEBASE_MULTI_WORKSPACE_FEDERATION.md`](adr/ADR_006_SHARED_LAKEBASE_MULTI_WORKSPACE_FEDERATION.md)
for why children connect directly to the master's Lakebase over Postgres
(instead of HTTP between apps).

### Admin page access (`--admins`)

The **Admin** page (`/api/admin/*`) is gated by an allowlist, independent of
Event Mode and applies to **every deploy**, including the legacy adoption app.

Admins are stored in **Lakebase** (the `quest_admins` table), which is the
shared source of truth. In federation the master owns it and child apps read it
through the shared event-writer credential — so **an admin is automatically an
admin across the standalone app, the master, and every child workspace**. The
effective admin set is the union of that table and the deploy-time env
allowlist (the bootstrap/fallback).

Seed the initial admins with `--admins`:

```bash
./deploy.sh --admins "alice@corp.com,bob@corp.com"
```

Behaviour:

- **`--admins` provided** → those emails are seeded into `quest_admins`; only
  admins see the Admin page (others get `403` and the nav item is hidden).
- **`--admins` omitted** → `deploy.sh` defaults to the **deploying user** (so
  the page is never open to everyone) — **except `--role child`**, which seeds
  nothing and inherits the admin list from the master's shared DB.

**Admins manage admins in-app.** The Admin page has an *Admin Access* card to
add/remove admins at runtime (or use the API directly):

```bash
# List admins
curl -s "$APP/api/admin/admins"
# Add an admin (any admin can grant admin; works from master or a child app)
curl -s -X POST "$APP/api/admin/admins" -H 'Content-Type: application/json' \
  -d '{"email":"carol@corp.com"}'
# Remove an admin — from the MASTER/standalone app only (child role has no
# DELETE on the shared table; last-admin removal is refused to avoid lockout)
curl -s -X DELETE "$APP/api/admin/admins/carol@corp.com"
```

Mechanics: `--admins` sets `QUEST_ADMIN_ALLOWLIST` in `app.yaml` (the seed +
fallback), and `/api/profile` returns `is_admin` so the frontend hides the nav.
The page is "open to all" only when nothing is configured anywhere — no env
allowlist **and** an empty/unreachable `quest_admins` (e.g. running locally
without `deploy.sh`).

---

## Deploy — single workspace

**Legacy adoption app (default).** No GameDay surface at all:

```bash
./deploy.sh
```

`/api/health` reports `"event_mode": false`; `/api/events/*`, `/api/host/*`, and
`/api/federation/*` return `404`.

**Single-workspace GameDay.** For a small event run entirely in one workspace,
add `--event-mode`:

```bash
./deploy.sh --event-mode
```

`/api/health` then reports `"event_mode": true` and migrations `001_gameday_core`
+ `002_federation` applied. You can then import a quest pack (below).

---

## Deploy — multi-workspace federation (large lab events)

One **master** app on the admin workspace owns the shared Lakebase; each attendee
workspace runs a **child** app that writes to it.

### 1. Deploy the master

```bash
./deploy.sh --role master --event anz-gameday
```

The master provisions its Lakebase, runs migrations, and **creates the shared
INSERT-only event-writer role** (`quest_event_writer`). It generates a random
secret for that role and prints a ready-to-copy child command in the success
banner — this is where the token comes from (see below).

### Where the event-writer token comes from

You do not invent or fetch the token separately — the **master deploy mints it
for you**. `deploy.sh --role master`:

1. generates a random secret (`python3 -c "import secrets; print(secrets.token_urlsafe(24))"`),
2. creates/updates the `quest_event_writer` Postgres role on the master Lakebase
   with that secret (INSERT on the fact tables + SELECT on the read views), and
3. prints it once in the success banner:

```
── Shared event-writer credential (give to child workspaces) ──

  Children deploy with these flags (rotate per event):

    ./deploy.sh \
      --role child \
      --event anz-gameday \
      --master-lakebase-host <master-host> \
      --master-lakebase-user quest_event_writer \
      --master-lakebase-token '<generated-secret>'
```

The `<generated-secret>` in that banner **is** the
`--master-lakebase-token <event-writer-credential>` you pass to children.

Important:
- **Shown once, not persisted.** The script does not store the secret. Copy it
  into your secret store immediately.
- **Lost it? Re-run the master deploy.** Provisioning does `ALTER ROLE ...
  PASSWORD` on re-run, so it rotates the secret — then redistribute the new one.
- **It's a Postgres role password, not an OAuth token.** Child workspace OAuth
  identities aren't accepted by the master's Lakebase, which is why a shared
  credential is used (see ADR_006). Treat it as **event-scoped and rotate per
  event**.

### 2. Deploy a child into each attendee workspace

Prefer passing the token via an environment variable / secret rather than inline,
so it doesn't land in shell history or process listings:

```bash
export QUEST_WRITER_TOKEN='<generated-secret>'   # from the master banner / your secret store

./deploy.sh --role child \
  --master-lakebase-host <master-lakebase-host> \
  --master-lakebase-user quest_event_writer \
  --master-lakebase-token "$QUEST_WRITER_TOKEN" \
  --event anz-gameday \
  --workspace-id ws-anzgt-01
```

Notes:
- Setting `--master-lakebase-host` alone defaults `--role` to `child`.
- `--master-lakebase-user` defaults to `quest_event_writer`; only pass it if you
  changed the role name on the master.
- The child **skips local Lakebase provisioning, migrations, and the scoring
  pipeline** — it only writes event facts to the shared DB.
- `--workspace-id` is how federated writes are attributed; it defaults to the
  workspace host if omitted. Give each attendee workspace a unique value.
- The child checks itself into `event_workspaces` on startup (a DB upsert, not an
  HTTP call), so it appears in the master's Workspaces health panel.
- **Verify before the event** — confirm reachability and that the credential is
  correctly INSERT-only:

  ```bash
  PGPASSWORD="$QUEST_WRITER_TOKEN" python3 scripts/federation_spike.py \
    --host <master-lakebase-host> --db quest_db --user quest_event_writer
  ```

### Full deploy flag reference

```bash
./deploy.sh --help
```

---

## Quest packs (works today)

Quests are configuration, not code. Author a manifest (see
[`samples/QUEST_PACK_SCHEMA.md`](samples/QUEST_PACK_SCHEMA.md) and
[`samples/SAMPLE_QUEST_PACK_AI_BI.md`](samples/SAMPLE_QUEST_PACK_AI_BI.md)) and
import it.

Lint locally without a server:

```bash
python scripts/lint_quest_pack.py quest_packs/built_in/ai_bi_gameday.yml
```

Lint / import / list against a running app (authenticated session or forwarded
identity headers):

```bash
# Lint
curl -sX POST "$APP_URL/api/host/quest-packs/lint" \
  -H 'Content-Type: application/json' \
  --data "$(python3 -c 'import json; print(json.dumps({"manifest_yaml": open("quest_packs/built_in/ai_bi_gameday.yml").read()}))')"

# Import (immutable per (slug, version) — bump the version to change content)
curl -sX POST "$APP_URL/api/host/quest-packs/import" \
  -H 'Content-Type: application/json' \
  --data "$(python3 -c 'import json; print(json.dumps({"manifest_yaml": open("quest_packs/built_in/ai_bi_gameday.yml").read()}))')"

# List
curl -s "$APP_URL/api/host/quest-packs"
```

---

## Run an event (works today — PR04)

Once a pack is imported, a host can run an event entirely over the API. Players
self-join; hosts can also place people on teams directly. Event status governs
play: **only an `active` event accepts attempts** (others return
`409 EVENT_NOT_ACTIVE`).

```bash
# 1. Create an event from an imported pack version (creator becomes owner-host)
curl -sX POST "$APP_URL/api/host/events" -H 'Content-Type: application/json' \
  --data '{"title":"ANZ GameDay","pack_version_id":"<packver_id>","slug":"anz-gameday"}'

# 2. Create teams
curl -sX POST "$APP_URL/api/host/events/anz-gameday/teams" \
  -H 'Content-Type: application/json' --data '{"name":"red","display_name":"Red Team"}'

# 3a. Bulk-import participants (teams created on demand, idempotent)
curl -sX POST "$APP_URL/api/host/events/anz-gameday/participants/import" \
  -H 'Content-Type: application/json' \
  --data '{"participants":[{"email":"ada@corp.com","team_name":"red"}]}'

# 3b. …or assign someone to a team directly (single team per event; reassigns)
curl -sX POST "$APP_URL/api/host/events/anz-gameday/teams/<team_id>/members" \
  -H 'Content-Type: application/json' --data '{"user_id":"ada@corp.com"}'

# 4. Open play, then players join + submit
curl -sX POST "$APP_URL/api/host/events/anz-gameday/start"
curl -sX POST "$APP_URL/api/events/anz-gameday/join" \
  -H 'Content-Type: application/json' --data '{"team_name":"red"}'

# 5. Lifecycle: pause | freeze | complete (and ready | archive)
curl -sX POST "$APP_URL/api/host/events/anz-gameday/freeze"
```

Lifecycle state machine: `draft → ready → active → paused/frozen → completed →
archived`. `start` stamps `starts_at`, `complete` stamps `ends_at`, `freeze`
stamps `scoring_frozen_at`. Players see joinable/active events at `GET /api/events`
and the lobby at `GET /api/events/{id}`. Every mutation writes an
`event_audit_log` row.

---

## Federation operations (master host)

Once children are deployed and an event exists, the host works from the master:

- **Import roster** — map generic lab users to real people/teams. CSV columns:
  `workspace_id` (or `workspace_host`), `lab_user_email`, `team_name`, optional
  `display_name`, `real_email`. Re-import is idempotent and re-attributes
  previously unmapped scores.

  ```bash
  curl -sX POST "$MASTER_APP_URL/api/host/events/<event_id>/roster/import" \
    -H 'Content-Type: application/json' \
    --data '{"csv":"workspace_id,lab_user_email,display_name,team_name\nws-anzgt-01,labuser+1@awsbricks.com,Ada Lovelace,Red Team"}'
  ```

- **Workspace health** — `GET /api/host/events/<event_id>/workspaces`
- **Unmapped identities** — `GET /api/host/events/<event_id>/identities/unmapped`

The same panels are available in the master's **Host Console** UI; children see
the event-wide leaderboard and their own team's rank in their **Event** tab.

Full operator runbook:
[`docs/10_EVENT_OPERATIONS_PLAYBOOK.md`](docs/10_EVENT_OPERATIONS_PLAYBOOK.md).

---

## Testing

Tiered, because end-to-end gameplay depends on PR03 + PR04:

**Tier 0 — local, no Databricks (works now):**

```bash
cd frontend && npm install && npm run build
python -m compileall app notebooks
pytest tests/          # federation unit tests
```

**Tier 1 — infra, on deploy (works now):**
- Standalone/master/child boot; `/api/health` shows migrations `001` + `002`.
- Migration 002 is idempotent (re-run safe).
- **Connectivity + credential scope** — from a child, verify reachability, auth,
  and that the writer role is INSERT-only:

  ```bash
  python scripts/federation_spike.py --host <master-host> --db quest_db --user quest_event_writer
  ```

- Child startup check-in appears in the master Workspaces panel.

**Tier 2 — federation reads (needs an `events` row):**
- Roster import → teams/participants/identity map; `/api/federation/status`
  resolves a team; `unmapped_identities` and `event_leaderboard` views populate
  once a `scoring_events` row exists.
- Until PR04 adds event creation, seed an `events` row (and a sample
  `scoring_events` row) directly to exercise these.

**Tier 3 — full end-to-end (works now — PR03 + PR04):**
- Host creates event + teams, players join, a player completes a quest →
  validated → scored → appears on the master leaderboard → child sees its rank.
- Standalone: same flow in one workspace (`--event-mode`). Verified via the
  event-run API flow above; non-active statuses block attempts.

---

## Reference

- Architecture: [`docs/05_TARGET_ARCHITECTURE.md`](docs/05_TARGET_ARCHITECTURE.md)
- Data model: [`docs/07_DATA_MODEL.md`](docs/07_DATA_MODEL.md)
- API contract: [`docs/08_API_CONTRACT.md`](docs/08_API_CONTRACT.md)
- Quest model + validation engine: [`docs/06_QUEST_MODEL_AND_VALIDATION_ENGINE.md`](docs/06_QUEST_MODEL_AND_VALIDATION_ENGINE.md)
- PR sequence: [`docs/13_PR_ALIGNED_SPRINT_PLAN.md`](docs/13_PR_ALIGNED_SPRINT_PLAN.md)
- Federation decision: [`adr/ADR_006_SHARED_LAKEBASE_MULTI_WORKSPACE_FEDERATION.md`](adr/ADR_006_SHARED_LAKEBASE_MULTI_WORKSPACE_FEDERATION.md)
