# ADR 005 — Workspace-Local First Deployment

## Status

Proposed

## Context

The target use cases include customer workshops and hunter-account motions. Requiring account-level provisioning or a central SaaS deployment would slow adoption and create security friction.

## Decision

The MVP will run inside a single Databricks workspace using workspace-local resources.

## Consequences

### Positive

- Lower customer trust barrier.
- Easier to deploy for field teams.
- Uses existing workspace identity.
- Avoids multi-tenant SaaS complexity.

### Negative

- Cross-workspace event orchestration is out of scope initially.
- Field teams may need per-workspace deployment.
- Central reporting requires export/sync later.

## Implementation notes

- Use current Databricks App deployment model.
- Use workspace-authenticated users.
- Use Unity Catalog for event resources.
- Use Lakebase in the same workspace.
- Keep account-level workspace provisioning as a future option.
