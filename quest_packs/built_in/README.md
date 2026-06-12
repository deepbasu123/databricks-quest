# Quest Pack Catalog

The canonical, importable GameDay catalog. Every pack here is **strict-lint
clean** (`tests/test_sample_packs.py` gates it in CI), carries machine-playable
`solutions`, and merges only after `scripts/preflight_event.py` plays it green
on a real workspace. `samples/packs/` mirrors the two doc-referenced worked
examples byte-for-byte.

## The catalog

| Pack | Tier | Audience | Duration | Quests/Tasks | Points |
|---|---|---|---|---|---|
| [`lakehouse_foundations`](lakehouse_foundations.yml) | Foundation | new-hires, workshops | 90 min | 4 / 8 | 650 |
| [`ai_bi_gameday`](ai_bi_gameday.yml) | Foundation | SEs, analysts | 120 min | 3 / 7 | 550 |
| [`lakeflow_pipelines_gameday`](lakeflow_pipelines_gameday.yml) | Foundation | data engineers | 120 min | 4 / 8 | 725 |
| [`genie_deep_dive`](genie_deep_dive.yml) | Strategic | SEs, analytics engineers | 150 min | 4 / 8 | 975 |
| [`ai_gateway_gameday`](ai_gateway_gameday.yml) | Strategic | platform owners, ML eng | 120 min | 4 / 8 | 900 |
| [`lakebase_roundtrip`](lakebase_roundtrip.yml) | Strategic | app devs, data engineers | 120 min | 4 / 8 | 950 |
| [`agent_bricks_gameday`](agent_bricks_gameday.yml) | Strategic | GenAI engineers | 150 min | 4 / 8 | 900 |

## Recommended progression

Cross-pack gating is a host convention (the platform gates quests within a
pack only):

```
lakehouse-foundations ──► ai-bi-gameday ──► genie-deep-dive ──► agent-bricks-gameday
        │
        └────────► lakeflow-pipelines-gameday ──► lakebase-roundtrip

  any foundation pack ─────────────────────────► ai-gateway-gameday
```

## Per-pack prerequisites & permissions matrix

What the **app service principal** needs (for validators) and what the
**host/teams** need (for play). Every SDK/REST check degrades to host review
when it can't run — a missing grant slows hosts down, it never blocks players.

| Pack | App SP needs (checks) | Host provisions (bootstrap) | Teams need |
|---|---|---|---|
| lakehouse-foundations | warehouse reads on team schemas; dashboards list | team schemas + seed (SQL lane) | warehouse, dashboard create |
| ai-bi-gameday | + Genie spaces list | same | + Genie space create |
| lakeflow-pipelines | + jobs/pipelines list | same | pipeline + job create, serverless entitlement |
| genie-deep-dive | + Genie space visibility (curation export, conversation listing — CAN MANAGE or team-group membership) | same | Genie create/curate, Conversation API access |
| ai-gateway-gameday | + serving endpoints CAN_VIEW (gateway config reads); the `rest_api` validator queries team endpoints | + **one serving endpoint per team** (`resources.workspace`) | CAN_MANAGE on their endpoint, `ai_query` on the warehouse |
| lakebase-roundtrip | + Database API reads (instance + synced-table status) | + **one shared Lakebase instance** (`resources.workspace`, cap 1) | a Postgres role each |
| agent-bricks-gameday | + beta `/api/2.0/tiles` reads; volume reads via warehouse | team schemas incl. a `docs` volume | Agent Bricks enablement, vector search capacity (~1 index/team), FM serving quota |

## Operating a pack

1. **Author/change** → `python scripts/lint_quest_pack.py --strict <pack>` (zero findings).
2. **SQL surface** → `python scripts/verify_pack_sql.py <pack> --warehouse-id <id> --catalog <writable>`.
3. **Full preflight (step 0 of every event)** → `python scripts/preflight_event.py --app-url <url> --pack <pack> --warehouse-id <id>` — exit 0 = winnable today; exit 2 = `human:` steps pending (run with `--interactive`); exit 1 = fix before the event.
4. **Event ops** → `scripts/provision_event.py` (`plan`/`bootstrap`/`reset`/`teardown`) or the host console.

## Versioning

`(slug, version)` is immutable once imported. Semver: validator/check changes =
**minor**; narrative/hint typos = **patch**; never reuse a version. The two
mirrored packs (`ai_bi_gameday`, `lakehouse_foundations`) must stay
byte-identical with `samples/packs/` — edit one, `cp` to the other
(`test_mirrored_packs_do_not_drift` enforces it).
