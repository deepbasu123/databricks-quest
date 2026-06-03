# 12 — Security, Governance, and Cost

## Security principles

1. **Least privilege by default.** Validators should run with only the permissions they need.
2. **Team isolation.** Teams should not be able to tamper with other teams' resources.
3. **Validation safety.** Player submissions must not result in arbitrary privileged code execution.
4. **Audit everything.** Event actions, validation attempts, hints, and score adjustments must be auditable.
5. **Workspace-local deployment.** MVP should avoid cross-account or multi-tenant trust complexity.

## Identity model

Use the Databricks App forwarded identity headers to identify the current user, as the current app does.

Map users to event roles:

- player
- team captain
- host
- admin

Role assignments are stored in Lakebase.

## Service principals

Recommended service principals:

| Principal | Purpose | Permissions |
|---|---|---|
| App SP | Serve API and read/write Lakebase | Lakebase app schema access |
| Validator SP | Run validators and inspect workspace objects | scoped to event resources and allowed system tables |
| Bootstrap SP | Create/reset event resources | scoped to configured event catalog/schema |

MVP can use one app/validator principal if necessary, but the architecture should not assume one forever.

## Resource isolation

Recommended pattern:

```text
catalog: quest_events
schema per event/team: evt_<event_slug>__team_<team_slug>
```

Or, for customers with catalog creation constraints:

```text
catalog: existing_catalog
schema: quest_<event_slug>_<team_slug>
```

Every validator should use templated team variables rather than trusting arbitrary user-submitted object names.

## SQL validator safety

Default SQL validator rules:

- allow `SELECT`, `SHOW`, `DESCRIBE`, `EXPLAIN`
- block `DROP`, `DELETE`, `TRUNCATE`, `ALTER`, `GRANT`, `REVOKE`, `CREATE`, `INSERT`, `UPDATE`, `MERGE` unless explicit host allowlist
- block multiple statements by default
- validate catalog/schema prefixes
- apply timeout
- truncate returned evidence

## Notebook/Python validator safety

Notebook and Python validators are higher risk.

Controls:

- only hosts can author/import them
- require explicit `trusted_validator: true`
- run in controlled serverless job/task environment
- pass only scoped parameters
- output must match normalized JSON contract
- timeouts are mandatory
- raw output stored in restricted logs only

## Anti-cheat considerations

MVP controls:

- team resource isolation
- scoring idempotency
- audit logs
- attempt limits
- hint penalties
- manual host review
- validation evidence

Future controls:

- anomaly detection over attempts
- suspicious cross-team object access
- unusually fast repeated passes
- copied object lineage
- validation tamper detection

## Governance

Unity Catalog should be used to:

- scope event resources
- manage grants
- track lineage where applicable
- store shared datasets
- separate event data from operational app data

Recommended catalogs:

```text
quest_app        # app audit and analytics tables
quest_events     # event participant/team resources
quest_content    # optional quest pack content assets
```

## Cost controls

Cost risks:

- too many validation jobs
- excessive SQL Warehouse usage
- large system-table queries
- abandoned serving endpoints
- team-created resources not cleaned up

Controls:

- use serverless where practical
- dry-run validators before event
- cap attempts per task
- cache static quest content
- run quick validators synchronously
- batch heavy validators
- enforce cleanup
- stop/delete event-created resources
- show cost estimate in host console

## Observability

Add logs/metrics for:

- API request latency
- Lakebase query latency
- validation queue depth
- validation duration
- validation pass/fail/error rate
- scoring events per minute
- leaderboard refresh time
- failed DB connections
- event resource bootstrap failures

## Audit trail

Audit events:

- quest pack imported
- event created/started/paused/frozen/completed
- team created
- participant joined
- attempt submitted
- validation run completed
- hint taken
- score adjustment applied
- announcement sent
- resources reset

## Data retention

Suggested defaults:

| Data | Retention |
|---|---:|
| operational active event state | retained until archived |
| attempt and validation facts | 12–24 months |
| raw validation logs | 30–90 days |
| post-event reports | customer/account policy dependent |
| team temporary resources | deleted after event unless retained |

## Security acceptance criteria

- Players cannot call host endpoints.
- Players cannot submit validation against another team's namespace.
- SQL validators reject destructive SQL by default.
- Every score adjustment has an audit record.
- Every validation result has an attempt record.
- Event freeze blocks new attempts.
- Cleanup never drops schemas outside configured event namespace.
