# ADR 001 — Keep Adoption Mode and Add Event Mode

## Status

Proposed

## Context

The current Databricks Quest app already provides value as an always-on platform adoption leaderboard. It scores user actions from system tables and presents dashboards, missions, badges, and leaderboards.

The new requirement is to support GameDay-style events with configurable quests and validation.

## Decision

Do not replace the current app. Add **GameDay Event Mode** alongside **Adoption Mode**.

## Consequences

### Positive

- Existing functionality remains usable.
- Migration risk is lower.
- Adoption scoring can become one validator type.
- Field teams can use the same product for ongoing adoption and live events.

### Negative

- The codebase must support two modes.
- Navigation and data model complexity increases.
- Care is needed to avoid confusing global missions with event tasks.

## Implementation notes

- Keep existing `/api/profile`, `/api/missions`, `/api/leaderboard` endpoints.
- Add `/api/events/*` and `/api/host/*` endpoints.
- Rename frontend concepts carefully: `Mission` for adoption, `QuestTask` for GameDay.
- Eventually represent current hard-coded missions as a built-in adoption quest pack.
