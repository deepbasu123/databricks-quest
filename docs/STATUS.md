# Project Status — Databricks Quest

**Single source of truth for where the build is.** Update this file as each PR
lands. Other docs (the PR plan, the GameDay README, per-doc implementation
notes) should point here rather than restating overall status.

- **Last updated:** 2026-06-03
- **Active branch / PR:** `feature/gameday-pr03-validation-engine` (stacked on PR #6)
- **Plan of record:** [`13_PR_ALIGNED_SPRINT_PLAN.md`](13_PR_ALIGNED_SPRINT_PLAN.md)

Legend: ✅ landed · 🟡 in progress · ⏳ planned (not started) · ⛔ blocked

---

## Modes

| Mode | Status | Notes |
|---|---|---|
| **Adoption Mode** | ✅ live | System-table scoring, missions, leaderboard, badges, admin. Unchanged. |
| **Event Mode (GameDay)** | 🟡 in progress | Schema, quest packs, validation/scoring write path (PR03), and multi-workspace federation plumbing landed; events/teams APIs (PR04) still needed for end-to-end play. |

---

## PR status

| PR | Capability | Status | Landed in |
|---|---|---|---|
| PR01 | GameDay domain model + Lakebase migrations + DB module | ✅ landed | `e6274d2` |
| PR02 | Configurable quest packs (manifest, loader, linter, import/list APIs, built-in pack) | ✅ landed | `2a959ab` |
| PR03 | Validation engine core (validator abstraction, SQL + manual validators, attempt submission, scoring idempotency) | ✅ landed | `feature/gameday-pr03-validation-engine` |
| PR04 | Event & team management (create events/teams/participants, join flow, lifecycle) | ⏳ planned | — |
| PR05 | Player gameplay experience (lobby, team dashboard, quest runner, submit UI) | ⏳ planned | — |
| PR06 | Admin host console (start/pause/freeze/complete, validation monitor, announcements) | ⏳ planned | — |
| PR07 | Live scoring & leaderboard (real-time leaderboard, freeze semantics) | ⏳ planned | — |
| PR08 | Resource bootstrap & reset (team schema/data, dry-run, scoped cleanup) | ⏳ planned | — |
| PR09 | Sample GameDay packs (AI/BI + Lakehouse Foundations) | ⏳ planned | — |
| PR10 | Security, observability, audit | ⏳ planned | — |
| PR11 | Field reporting & hunter signaling (export, sales signal) | ⏳ planned | — |
| PR12 | Hardening, release, docs | ⏳ planned | — |

### Multi-workspace federation (ADR_006)

Federation was specced as PR13–PR16 but the **plumbing landed early on the PR02
branch** so later work wouldn't have to migrate twice. Gameplay-dependent parts
remain gated on PR03/PR04.

| PR | Capability | Status | Landed in |
|---|---|---|---|
| PR13 | Federation foundation (`QUEST_ROLE` seam, writer-credential branch, migration 002, deploy role flags) | ✅ landed | `2a959ab` |
| PR14 | Child wiring + shared writer credential (write stamping, deterministic idempotency, startup check-in, event-writer role) | ✅ landed | `2a959ab` |
| PR15 | Roster + identity reconciliation (roster CSV import, unmapped-identities + workspace-health endpoints) | ✅ landed | `2a959ab` |
| PR16 | Federation UX (child event leaderboard + own-team rank, master host console panels) | ✅ landed | `2a959ab` |

---

## What's deployable / testable today

See [`README_GAMEDAY.md`](../README_GAMEDAY.md#testing) for commands. Tiers:

- **Tier 0 — local, no Databricks** ✅: `pytest tests/` (80), `compileall`, frontend build, offline quest-pack lint. PR03 adds SQL-safety, expectation, dispatch, and scoring-idempotency suites (all pure / fake-DB).
- **Tier 1 — infra on deploy** ✅: standalone/master/child boot; `/api/health` shows migrations `001`+`002`+`003`; migration idempotency; quest-pack lint/import; connectivity + INSERT-only credential scope (`scripts/federation_spike.py`); child startup check-in in the master Workspaces panel.
- **Tier 2 — federation reads + gameplay write path** 🟡 (needs an `events` row + a team seeded by SQL until PR04): attempt submission `POST /api/events/{id}/tasks/{task_id}/attempts` (SQL validator needs a warehouse — set `QUEST_SQL_WAREHOUSE_ID`; manual validator works with no warehouse), `validation_results`/`scoring_events` written, idempotent re-submit; roster import, `/api/federation/status` team resolution, `event_leaderboard` + `unmapped_identities` views.
- **Tier 3 — full end-to-end** ⛔ blocked on **PR04** (events/teams APIs): child plays → validated → scored → master leaderboard → child sees rank. The write path exists (PR03); it just needs the event/team rows PR04 will create through the API instead of seed SQL.

---

## Known gaps / next steps

- **Scoring write path landed (PR03)** — submitting an attempt validates, persists `task_attempts` + `validation_results`, and awards base points once via `scoring_events` (idempotent per team/workspace). Manual validators return a pending state; the host manual-override UI is a later PR.
- **SQL validator needs a warehouse** — set `QUEST_SQL_WAREHOUSE_ID` (or per-validator `warehouse_id`); without one, `sql_assertion` returns a player-safe `error` and the host sees the diagnostic in `validation_results.private_message`. Safety/templating/expectation logic is warehouse-independent and unit-tested.
- **No event/team APIs yet** — event creation lives in PR04; attempt submission + roster import require an existing `event_id` and (standalone) a team + team_member (seed via SQL to test Tier 2 early).
- **Federation writer credential** is a single shared secret distributed per event; rotate per event (see ADR_006). Per-child OAuth roles are a future hardening option.
- **Endpoints present today:** `/api/health`, adoption reads, `/api/host/quest-packs/*`, `/api/events/{id}/tasks/{task_id}/attempts` (POST), `/api/events/{id}/attempts/{attempt_id}` (GET), `/api/federation/status|leaderboard`, `/api/host/events/{id}/roster|workspaces|identities/unmapped`.

---

## Reference

- PR plan: [`13_PR_ALIGNED_SPRINT_PLAN.md`](13_PR_ALIGNED_SPRINT_PLAN.md)
- GameDay deploy/ops: [`../README_GAMEDAY.md`](../README_GAMEDAY.md)
- Architecture: [`05_TARGET_ARCHITECTURE.md`](05_TARGET_ARCHITECTURE.md) · Data model: [`07_DATA_MODEL.md`](07_DATA_MODEL.md) · API: [`08_API_CONTRACT.md`](08_API_CONTRACT.md)
- ADRs: [`../adr/`](../adr/)
