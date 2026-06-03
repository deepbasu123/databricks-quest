"""Runtime configuration for Databricks Quest — the single mode switch.

There is exactly one codebase and one build artifact. Standalone vs
multi-workspace, and master vs child, are selected purely by runtime
parameters (env vars set from ``deploy.sh`` flags). This module reads them
**once at import** so every other module shares the same view.

``QUEST_ROLE`` is the only mode switch:

- ``standalone`` (default) — single-workspace app against its own local
  Lakebase. Federation paths are dormant; behaviour is exactly as before.
- ``master`` — runs against the shared Lakebase it owns; hosts the console,
  reports, roster/identity map, and provisions the event-writer credential.
- ``child`` — runs gameplay + validation locally but reads/writes the master's
  shared Lakebase (scoped by ``QUEST_WORKSPACE_ID``).

Everything else (Lakebase host, writer credential, event slug, workspace id) is
an independent parameter, so the same image can be re-pointed at a different
master/event without a rebuild.
"""

import os

VALID_ROLES = ("standalone", "master", "child")

QUEST_ROLE = os.getenv("QUEST_ROLE", "standalone").strip().lower() or "standalone"
if QUEST_ROLE not in VALID_ROLES:
    QUEST_ROLE = "standalone"

# Identifier of the workspace this app instance runs in. Required for child
# deployments so federated writes can be attributed back to a workspace.
QUEST_WORKSPACE_ID = os.getenv("QUEST_WORKSPACE_ID", "").strip()

# The event this deployment is wired to (slug). Children resolve their own team
# and the overall leaderboard from this.
QUEST_EVENT_SLUG = os.getenv("QUEST_EVENT_SLUG", "").strip()

# Optional human-facing app URL + version, surfaced in the workspace check-in so
# the host console can deep-link and track versions across child workspaces.
QUEST_APP_URL = os.getenv("QUEST_APP_URL", "").strip()
QUEST_APP_VERSION = os.getenv("QUEST_APP_VERSION", "").strip()


def is_standalone() -> bool:
    return QUEST_ROLE == "standalone"


def is_master() -> bool:
    return QUEST_ROLE == "master"


def is_child() -> bool:
    return QUEST_ROLE == "child"


def summary() -> dict:
    """Small, non-sensitive snapshot of the runtime role for /api/health etc."""
    return {
        "role": QUEST_ROLE,
        "workspace_id": QUEST_WORKSPACE_ID or None,
        "event_slug": QUEST_EVENT_SLUG or None,
        "app_version": QUEST_APP_VERSION or None,
    }
