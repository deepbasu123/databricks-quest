#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Databricks Quest — One-Shot Deployment
#
# Deploys the full Quest gamification app to your Databricks workspace.
# Handles everything: auth check, warehouse selection, frontend build,
# bundle deploy, scoring pipeline, permissions, and app startup.
#
# Usage:
#   ./deploy.sh                          # Interactive (prompts for everything)
#   ./deploy.sh --warehouse "My WH"      # Skip warehouse prompt
#   ./deploy.sh --catalog quest_data     # Specify catalog name
#   ./deploy.sh --profile my-profile     # Use a specific CLI profile
#   ./deploy.sh --app-name my-quest      # Custom app name
#
# Requirements:
#   - Databricks CLI v0.200+ (brew install databricks/tap/databricks)
#   - Node.js 18+ (brew install node)
#   - Authenticated CLI session (script will prompt if needed)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────
APP_NAME="databricks-quest"
QUEST_CATALOG=""
QUEST_SCHEMA="quest"
WAREHOUSE_NAME=""
WAREHOUSE_ID=""
PROFILE_FLAG=""
PROFILE_NAME=""
LAKEBASE_HOST=""
LAKEBASE_DB="quest_db"
TARGET="dev"
SKIP_BUILD=""
SKIP_SCORING=""
SKIP_AUTH_CHECK=""
DEPLOY_MODE=""  # "full" (DAB bundle) or "quick" (direct API, like Forge)

# ── Event Mode (GameDay) — opt-in master switch (legacy adoption is default) ──
# Off unless --event-mode is passed or a master/child role is selected. When
# off, the deployed app exposes ZERO GameDay surface (Event APIs 404, Event UI
# hidden) and GameDay migrations are skipped — i.e. the legacy adoption app.
QUEST_EVENT_MODE=""         # "on" enables Event Mode; empty = off (legacy)

# ── Admin allowlist ──────────────────────────────────────────────────────────
# Comma-separated emails allowed to see the adoption Admin page (/api/admin/*).
# If left empty, deploy.sh defaults it to the deploying user after auth so the
# Admin page is never wide open in a deployment.
QUEST_ADMIN_ALLOWLIST=""    # e.g. "alice@corp.com,bob@corp.com"

# ── Federation (ADR_006) — one codebase, role selected by these flags ────────
QUEST_ROLE=""               # standalone (default) | master | child
MASTER_LAKEBASE_HOST=""     # child: the MASTER workspace's shared Lakebase host
MASTER_LAKEBASE_TOKEN=""    # child: the shared event-writer credential/secret
MASTER_LAKEBASE_USER="quest_event_writer"  # child: writer role name
EVENT_SLUG=""               # event this deployment is wired to
WORKSPACE_ID=""             # child: id used to attribute federated writes
EVENT_WRITER_PASSWORD=""    # master: generated secret for the writer role

# ── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# ── Helpers ──────────────────────────────────────────────────────────────────
info()    { echo -e "${BLUE}▸${NC} $*"; }
success() { echo -e "${GREEN}✓${NC} $*"; }
warn()    { echo -e "${YELLOW}!${NC} $*"; }
fail()    { echo -e "${RED}✗${NC} $*"; exit 1; }
step()    { echo -e "\n${BOLD}${CYAN}── $* ──${NC}\n"; }

# Apply GameDay schema migrations to Lakebase (idempotent; safe to re-run).
# Args: <host> <db> <user> <token>. Non-fatal — adoption mode works without it.
run_gameday_migrations() {
  local host="$1" db="$2" user="$3" token="$4"
  local runner="${SCRIPT_DIR:-$(pwd)}/app/migrations/run_migrations.py"
  if [ ! -f "$runner" ]; then
    warn "Migration runner not found ($runner) — skipping GameDay migrations."
    return 0
  fi
  if [ -z "$host" ] || [ -z "$token" ]; then
    warn "Lakebase host/token unavailable — skipping GameDay migrations."
    return 0
  fi
  info "Applying GameDay schema migrations..."
  if PGPASSWORD="$token" python3 "$runner" \
      --lakebase-host "$host" \
      --lakebase-db "$db" \
      --user "$user"; then
    success "GameDay migrations applied"
  else
    warn "GameDay migrations failed (non-fatal). Re-run later with:
    PGPASSWORD=<token> python3 app/migrations/run_migrations.py \\
      --lakebase-host $host --lakebase-db $db --user $user"
  fi
}

# Provision the shared INSERT-only event-writer Postgres role on the MASTER
# Lakebase and grant it exactly the privileges a child app needs (ADR_006):
#   INSERT  on the four event-fact tables
#   SELECT  on the leaderboard read surface (so children can render the
#           event-wide leaderboard and locate their own team's rank)
#   INSERT/UPDATE/SELECT on event_workspaces (for the startup check-in upsert)
#   SELECT/INSERT on quest_admins (shared admin allowlist — children read it so
#           admins are global, and admins can ADD admins from a child; removal
#           requires the master/standalone app — no DELETE for the writer role)
# No UPDATE/DELETE on facts, no access to secrets. Idempotent: re-running
# resets the role's password (rotate-per-event) and re-applies grants.
# Args: <host> <db> <admin_user> <admin_token> <writer_user> <writer_password>
provision_event_writer_role() {
  local host="$1" db="$2" admin_user="$3" admin_token="$4" writer="$5" wpass="$6"
  if [ -z "$host" ] || [ -z "$admin_token" ]; then
    warn "Lakebase host/token unavailable — skipping event-writer role provisioning."
    return 1
  fi
  info "Provisioning shared event-writer role '$writer'..."
  # Escape single quotes for safe SQL string literals.
  local esc_pass="${wpass//\'/\'\'}"
  local esc_writer="${writer//\'/\'\'}"
  if PGPASSWORD="$admin_token" psql "host=$host port=5432 dbname=$db user=$admin_user sslmode=require" \
      -v ON_ERROR_STOP=1 -q -c "
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$esc_writer') THEN
    EXECUTE format('CREATE ROLE %I WITH LOGIN PASSWORD %L', '$esc_writer', '$esc_pass');
  ELSE
    EXECUTE format('ALTER ROLE %I WITH LOGIN PASSWORD %L', '$esc_writer', '$esc_pass');
  END IF;
  EXECUTE format('GRANT INSERT ON scoring_events, task_attempts, validation_results, hints_taken TO %I', '$esc_writer');
  EXECUTE format('GRANT INSERT, UPDATE, SELECT ON event_workspaces TO %I', '$esc_writer');
  EXECUTE format('GRANT SELECT ON event_leaderboard, team_scores, teams, participant_identity_map, events, announcements TO %I', '$esc_writer');
  EXECUTE format('GRANT SELECT ON quest_packs, quest_pack_versions, quests, quest_tasks, task_hints, task_validators TO %I', '$esc_writer');
  -- Shared admin allowlist: children read it (so admins are global) and may
  -- ADD admins through the app. No DELETE — removing an admin requires the
  -- master/standalone app (full workspace identity); a leaked child credential
  -- must never be able to delete. Guarded so a missing table (migrations not
  -- yet applied) doesn't abort role provisioning.
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'quest_admins') THEN
    EXECUTE format('GRANT SELECT, INSERT ON quest_admins TO %I', '$esc_writer');
  END IF;
END \$\$;
"; then
    success "Event-writer role '$writer' provisioned and granted (INSERT-only on facts)"
    return 0
  fi
  warn "Event-writer role provisioning failed (non-fatal). Provision it manually before the event,
    then verify with: python3 scripts/federation_spike.py --host $host --db $db --user $writer"
  return 1
}

# Write app/app.yaml with role-appropriate env. Standalone emits exactly the
# two-variable file it always has (byte-for-byte unchanged). master/child add
# the federation env (QUEST_ROLE, workspace id, event slug, and — for child —
# the explicit writer credential pointed at the master's shared Lakebase).
# Args: <lakebase_host> <lakebase_db>
write_app_yaml() {
  local lb_host="$1" lb_db="$2"
  local app_yaml="${SCRIPT_DIR:-$(pwd)}/app/app.yaml"
  {
    echo "command:"
    echo "  - uvicorn"
    echo "  - main:app"
    echo "  - --host"
    echo "  - 0.0.0.0"
    echo "  - --port"
    echo "  - \"8000\""
    echo ""
    echo "env:"
    echo "  - name: LAKEBASE_HOST"
    echo "    value: \"$lb_host\""
    echo "  - name: LAKEBASE_DB"
    echo "    value: \"$lb_db\""
    # SQL warehouse the sql_assertion validators execute against (PR03). Emitted
    # for every role when known so deterministic SQL checks have a target.
    [ -n "$WAREHOUSE_ID" ] && { echo "  - name: QUEST_SQL_WAREHOUSE_ID"; echo "    value: \"$WAREHOUSE_ID\""; }
    # Admin allowlist for /api/admin/* (comma-separated emails). When set, the
    # Admin page and its APIs are restricted to these users; absence = open.
    [ -n "$QUEST_ADMIN_ALLOWLIST" ] && { echo "  - name: QUEST_ADMIN_ALLOWLIST"; echo "    value: \"$QUEST_ADMIN_ALLOWLIST\""; }
    # Event Mode master switch. Only emitted when ON; absence = legacy default,
    # so a standalone adoption deploy keeps its exact two-variable app.yaml.
    if [ "$QUEST_EVENT_MODE" = "on" ]; then
      echo "  - name: QUEST_EVENT_MODE"
      echo "    value: \"on\""
    fi
    if [ -n "$QUEST_ROLE" ] && [ "$QUEST_ROLE" != "standalone" ]; then
      echo "  - name: QUEST_ROLE"
      echo "    value: \"$QUEST_ROLE\""
      [ -n "$EVENT_SLUG" ]   && { echo "  - name: QUEST_EVENT_SLUG"; echo "    value: \"$EVENT_SLUG\""; }
      [ -n "$WORKSPACE_ID" ] && { echo "  - name: QUEST_WORKSPACE_ID"; echo "    value: \"$WORKSPACE_ID\""; }
      if [ "$QUEST_ROLE" = "child" ]; then
        echo "  - name: LAKEBASE_USER"
        echo "    value: \"$MASTER_LAKEBASE_USER\""
        echo "  - name: LAKEBASE_PASSWORD"
        echo "    value: \"$MASTER_LAKEBASE_TOKEN\""
      fi
    fi
  } > "$app_yaml"
}

# ── Parse Arguments ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --app-name)       APP_NAME="$2"; shift 2 ;;
    --catalog)        QUEST_CATALOG="$2"; shift 2 ;;
    --schema)         QUEST_SCHEMA="$2"; shift 2 ;;
    --warehouse)      WAREHOUSE_NAME="$2"; shift 2 ;;
    --warehouse-id)   WAREHOUSE_ID="$2"; shift 2 ;;
    --profile)        PROFILE_NAME="$2"; PROFILE_FLAG="--profile $2"; shift 2 ;;
    --target)         TARGET="$2"; shift 2 ;;
    --lakebase-host)  LAKEBASE_HOST="$2"; shift 2 ;;
    --lakebase-db)    LAKEBASE_DB="$2"; shift 2 ;;
    --skip-build)     SKIP_BUILD=1; shift ;;
    --skip-scoring)   SKIP_SCORING=1; shift ;;
    --skip-auth-check) SKIP_AUTH_CHECK=1; shift ;;
    --quick)          DEPLOY_MODE="quick"; shift ;;
    --full)           DEPLOY_MODE="full"; shift ;;
    --event-mode)         QUEST_EVENT_MODE="on"; shift ;;
    --admins)             QUEST_ADMIN_ALLOWLIST="$2"; shift 2 ;;
    --role)               QUEST_ROLE="$2"; shift 2 ;;
    --master-lakebase-host)  MASTER_LAKEBASE_HOST="$2"; shift 2 ;;
    --master-lakebase-token) MASTER_LAKEBASE_TOKEN="$2"; shift 2 ;;
    --master-lakebase-user)  MASTER_LAKEBASE_USER="$2"; shift 2 ;;
    --event)              EVENT_SLUG="$2"; shift 2 ;;
    --workspace-id)       WORKSPACE_ID="$2"; shift 2 ;;
    --help|-h)
      echo "Usage: ./deploy.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --app-name NAME       App name (default: databricks-quest)"
      echo "  --catalog NAME        Unity Catalog name for Quest data"
      echo "  --schema NAME         Schema name (default: quest)"
      echo "  --warehouse NAME      SQL Warehouse name (interactive if omitted)"
      echo "  --warehouse-id ID     SQL Warehouse ID (skips warehouse lookup)"
      echo "  --profile NAME        Databricks CLI profile to use"
      echo "  --target TARGET       Bundle target: dev or prod (default: dev)"
      echo "  --lakebase-host HOST  Lakebase endpoint host (optional, for faster reads)"
      echo "  --lakebase-db NAME    Lakebase database name (default: quest_db)"
      echo "  --quick               Quick deploy: direct API (no DAB bundle, like Forge)"
      echo "  --full                Full deploy: DAB bundle with scoring job (default)"
      echo "  --skip-build          Skip frontend build (use existing app/static/)"
      echo "  --skip-scoring        Skip running the scoring pipeline"
      echo "  --skip-auth-check     Skip authentication validation (use if already authenticated)"
      echo "  --admins EMAILS       Comma-separated emails seeded as Admin-page admins."
      echo "                        Defaults to the deploying user when omitted (except"
      echo "                        --role child, which inherits admins from the master)."
      echo "                        Stored in Lakebase (quest_admins) and shared across"
      echo "                        master/child; admins can add more admins in-app."
      echo ""
      echo "Event Mode (GameDay) — opt-in; legacy adoption app is the default:"
      echo "  --event-mode             Enable GameDay/Event Mode (default: OFF)."
      echo "                           When OFF, Event APIs 404, Event UI is hidden,"
      echo "                           and GameDay migrations are skipped."
      echo "                           Implied by --role master|child."
      echo ""
      echo "Multi-workspace federation (ADR_006 — one codebase, role-driven):"
      echo "  --role ROLE              standalone (default) | master | child"
      echo "  --event SLUG             Event slug this deployment is wired to"
      echo "  --workspace-id ID        Child: id used to attribute federated writes"
      echo "                           (defaults to the workspace host if omitted)"
      echo "  --master-lakebase-host H Child: the MASTER workspace's shared Lakebase host"
      echo "                           (setting this defaults --role to child)"
      echo "  --master-lakebase-token T Child: shared event-writer credential/secret"
      echo "  --master-lakebase-user U Child: writer role name (default: quest_event_writer)"
      echo ""
      echo "  master: provisions its own Lakebase, runs migrations, creates the shared"
      echo "          event-writer role and prints its credential to hand to children."
      echo "  child:  skips local Lakebase + migrations; points at the master Lakebase"
      echo "          with the writer credential and stamps writes with --workspace-id."
      echo "  --help, -h            Show this help message"
      exit 0
      ;;
    *) fail "Unknown option: $1. Run ./deploy.sh --help for usage." ;;
  esac
done

# ── Resolve federation role (ADR_006) ────────────────────────────────────────
# Role precedence: explicit --role wins; else presence of --master-lakebase-host
# implies child; else standalone (today's default, unchanged).
if [ -z "$QUEST_ROLE" ]; then
  if [ -n "$MASTER_LAKEBASE_HOST" ]; then
    QUEST_ROLE="child"
  else
    QUEST_ROLE="standalone"
  fi
fi
case "$QUEST_ROLE" in
  standalone|master|child) ;;
  *) fail "Invalid --role '$QUEST_ROLE'. Use standalone, master, or child." ;;
esac

# ── Resolve Event Mode (GameDay) ─────────────────────────────────────────────
# Opt-in: master/child roles imply it; otherwise it is off unless --event-mode
# was passed. Off = legacy adoption app (no GameDay surface, no GameDay schema).
if [ "$QUEST_ROLE" = "master" ] || [ "$QUEST_ROLE" = "child" ]; then
  QUEST_EVENT_MODE="on"
fi

if [ "$QUEST_ROLE" = "child" ]; then
  [ -n "$MASTER_LAKEBASE_HOST" ] || fail "child role requires --master-lakebase-host."
  [ -n "$MASTER_LAKEBASE_TOKEN" ] || fail "child role requires --master-lakebase-token (shared event-writer credential)."
  # Point the app's Lakebase layer at the master's shared endpoint. Setting
  # LAKEBASE_HOST here makes the script take the "provided Lakebase" path
  # (skips local provisioning); migrations are additionally skipped for child.
  LAKEBASE_HOST="$MASTER_LAKEBASE_HOST"
  # The child has only INSERT on the shared facts — it must never run the
  # adoption scoring pipeline or the Delta→Lakebase sync.
  SKIP_SCORING=1
fi

# ── Banner ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║         Databricks Quest — Deploy            ║${NC}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════╝${NC}"
echo ""

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1: Check Prerequisites
# ══════════════════════════════════════════════════════════════════════════════
step "Step 1/8: Checking prerequisites"

# Find Databricks CLI — prefer the newest version available
CLI=""
BEST_VERSION="0.0.0"

check_cli() {
  local path="$1"
  if [ -x "$path" ]; then
    local ver
    ver=$("$path" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo "0.0.0")
    # Prefer the strictly-higher version (no external deps)
    if [ "$ver" != "$BEST_VERSION" ] && \
       [ "$(printf '%s\n%s\n' "$BEST_VERSION" "$ver" | sort -V | tail -1)" = "$ver" ]; then
      CLI="$path"
      BEST_VERSION="$ver"
    fi
  fi
}

# Check all common locations
check_cli "/opt/homebrew/bin/databricks"
check_cli "/usr/local/bin/databricks"
if command -v databricks &>/dev/null; then
  check_cli "$(command -v databricks)"
fi

if [ -z "$CLI" ]; then
  fail "Databricks CLI not found. Install it:
    macOS:   brew install databricks/tap/databricks
    Linux:   curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh
    Docs:    https://docs.databricks.com/en/dev-tools/cli/install.html"
fi

CLI_VERSION="$BEST_VERSION"
CLI_MAJOR=$(echo "$CLI_VERSION" | cut -d. -f1)
CLI_MINOR=$(echo "$CLI_VERSION" | cut -d. -f2)
if [[ "$CLI_MAJOR" -eq 0 && "$CLI_MINOR" -lt 285 ]]; then
  fail "Databricks CLI v$CLI_VERSION is too old (need v0.285+ for Lakebase).
    Upgrade: brew upgrade databricks/tap/databricks"
fi
success "Databricks CLI v$CLI_VERSION ($CLI)"

# Check Node.js (optional — pre-built frontend is included in the repo)
if command -v node &>/dev/null; then
  success "Node.js $(node --version)"
  if command -v npm &>/dev/null; then
    success "npm $(npm --version)"
  fi
else
  info "Node.js not found (optional — pre-built frontend is included in the repo)"
fi

# Check psql (needed for Lakebase setup)
if ! command -v psql &>/dev/null; then
  fail "psql not found. Install PostgreSQL client:
    macOS:   brew install postgresql@16
    Linux:   apt install postgresql-client"
fi
success "psql ($(psql --version | head -1))"

# ── Deploy Mode Selection ────────────────────────────────────────────────────
if [ -z "$DEPLOY_MODE" ]; then
  echo ""
  echo -e "  ${BOLD}Choose deployment mode:${NC}"
  echo ""
  echo -e "  ${CYAN}1)${NC} ${BOLD}Full Deploy${NC} (recommended)"
  echo -e "     Uses Databricks Asset Bundles. Deploys the app, scoring notebook,"
  echo -e "     and a scheduled job that re-scores every 4 hours."
  echo ""
  echo -e "  ${CYAN}2)${NC} ${BOLD}Quick Deploy${NC}"
  echo -e "     Uses the Databricks Apps API directly (like Forge)."
  echo -e "     Deploys only the app. You run the scoring notebook manually."
  echo ""
  read -rp "  Select mode [1]: " MODE_CHOICE
  MODE_CHOICE="${MODE_CHOICE:-1}"
  if [ "$MODE_CHOICE" = "2" ]; then
    DEPLOY_MODE="quick"
  else
    DEPLOY_MODE="full"
  fi
fi

if [ "$DEPLOY_MODE" = "quick" ]; then
  success "Deploy mode: Quick (direct API)"
else
  success "Deploy mode: Full (DAB bundle + scoring job)"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2: Authenticate & Detect Workspace
# ══════════════════════════════════════════════════════════════════════════════
step "Step 2/8: Authenticating"

if [ -n "$SKIP_AUTH_CHECK" ]; then
  warn "Skipping authentication check (--skip-auth-check flag set)"
  info "Make sure you've already run: databricks auth login --host YOUR_WORKSPACE_URL"
  USER_EMAIL="(authentication skipped)"
else
  # Try to get current user — this validates authentication
  USER_JSON=""
  if [ -n "$PROFILE_FLAG" ]; then
    USER_JSON=$($CLI current-user me $PROFILE_FLAG -o json 2>/dev/null || true)
  else
    USER_JSON=$($CLI current-user me -o json 2>/dev/null || true)
  fi

  if [ -z "$USER_JSON" ] || ! echo "$USER_JSON" | python3 -c "import sys,json; json.load(sys.stdin)" &>/dev/null; then
    warn "Not authenticated. Let's log in now."
    echo ""
    read -rp "Enter your workspace URL (e.g. https://my-workspace.cloud.databricks.com): " WORKSPACE_URL
    WORKSPACE_URL="${WORKSPACE_URL%/}"

    if [ -z "$WORKSPACE_URL" ]; then
      fail "Workspace URL cannot be empty."
    fi

    info "Opening browser for authentication..."
    if [ -n "$PROFILE_NAME" ]; then
      $CLI auth login --host "$WORKSPACE_URL" --profile "$PROFILE_NAME"
    else
      $CLI auth login --host "$WORKSPACE_URL"
    fi

    # Retry — find the profile the CLI just created and use it explicitly.
    # The databricks.yml placeholder host blocks DATABRICKS_HOST and --host,
    # so the only reliable method is looking up the profile by workspace URL.
    if [ -n "$PROFILE_FLAG" ]; then
      USER_JSON=$($CLI current-user me $PROFILE_FLAG -o json 2>/dev/null || true)
    else
      # Scan auth profiles for one matching the workspace URL we just logged into
      for _profile in $($CLI auth profiles 2>/dev/null | grep "$WORKSPACE_URL" | awk '{print $1}'); do
        USER_JSON=$($CLI current-user me --profile "$_profile" -o json 2>/dev/null || true)
        if [ -n "$USER_JSON" ] && echo "$USER_JSON" | python3 -c "import sys,json; json.load(sys.stdin)" &>/dev/null 2>&1; then
          PROFILE_NAME="$_profile"
          PROFILE_FLAG="--profile $_profile"
          break
        fi
      done
    fi

    if [ -z "$USER_JSON" ]; then
      fail "Authentication failed. Please run: databricks auth login --host $WORKSPACE_URL"
    fi
  fi

  USER_EMAIL=$(echo "$USER_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('userName',''))")
  success "Authenticated as $USER_EMAIL"
fi

# Default the Admin allowlist to the deploying user so the Admin page is gated
# (never wide open) even when --admins is not passed. Only for a real email
# (skip the "(authentication skipped)" placeholder). Skipped for child apps:
# children inherit the shared admin list from the master's quest_admins table,
# so they must NOT seed their own deployer as a global admin.
if [ -z "$QUEST_ADMIN_ALLOWLIST" ] && [[ "$USER_EMAIL" == *"@"* ]] && [ "$QUEST_ROLE" != "child" ]; then
  QUEST_ADMIN_ALLOWLIST="$USER_EMAIL"
fi

# Get workspace host
if [ -n "$PROFILE_FLAG" ]; then
  WORKSPACE_HOST=$($CLI auth env $PROFILE_FLAG 2>/dev/null | python3 -c "
import sys,json
try:
    d = json.load(sys.stdin)
    print(d.get('env',{}).get('DATABRICKS_HOST',''))
except: pass
" 2>/dev/null || true)
fi

# Fallback: try to get host from CLI config
if [ -z "${WORKSPACE_HOST:-}" ]; then
  WORKSPACE_HOST=$($CLI auth env 2>/dev/null | python3 -c "
import sys,json
try:
    d = json.load(sys.stdin)
    print(d.get('env',{}).get('DATABRICKS_HOST',''))
except: pass
" 2>/dev/null || true)
fi

# Another fallback: read from profiles
if [ -z "${WORKSPACE_HOST:-}" ]; then
  if [ -n "$PROFILE_NAME" ]; then
    WORKSPACE_HOST=$(python3 -c "
import configparser, os
c = configparser.ConfigParser()
c.read(os.path.expanduser('~/.databrickscfg'))
print(c.get('$PROFILE_NAME', 'host', fallback=''))
" 2>/dev/null || true)
  fi
fi

if [ -z "${WORKSPACE_HOST:-}" ]; then
  warn "Could not auto-detect workspace host."
  read -rp "Enter your workspace URL: " WORKSPACE_HOST
  WORKSPACE_HOST="${WORKSPACE_HOST%/}"
fi

WORKSPACE_HOST="${WORKSPACE_HOST%/}"
success "Workspace: $WORKSPACE_HOST"

# Federation role echo + default the child workspace id from the workspace host.
if [ "$QUEST_ROLE" != "standalone" ]; then
  if [ -z "$WORKSPACE_ID" ]; then
    WORKSPACE_ID=$(echo "$WORKSPACE_HOST" | sed -E 's#^https?://##; s#/.*$##')
  fi
  success "Federation role: $QUEST_ROLE${EVENT_SLUG:+  event=$EVENT_SLUG}${WORKSPACE_ID:+  workspace_id=$WORKSPACE_ID}"
  if [ "$QUEST_ROLE" = "child" ]; then
    info "Child points at MASTER Lakebase: $MASTER_LAKEBASE_HOST/$LAKEBASE_DB (writer: $MASTER_LAKEBASE_USER)"
  fi
fi

# Export env var auth so CLI commands don't rely on the token cache
# (the cache can expire mid-operation during long Terraform applies)
CLI_TOKEN=$($CLI auth token $PROFILE_FLAG -o json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || true)
if [ -n "$CLI_TOKEN" ]; then
  export DATABRICKS_HOST="$WORKSPACE_HOST"
  export DATABRICKS_TOKEN="$CLI_TOKEN"
  export DATABRICKS_AUTH_TYPE=pat
  # Clear profile flag so CLI uses env vars instead
  PROFILE_FLAG=""
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3: Select SQL Warehouse
# ══════════════════════════════════════════════════════════════════════════════
step "Step 3/8: Selecting SQL Warehouse"

if [ -n "$WAREHOUSE_ID" ]; then
  success "Using warehouse ID: $WAREHOUSE_ID (provided via --warehouse-id)"
else
  # List available warehouses
  info "Discovering SQL Warehouses..."
  WAREHOUSES_JSON=$($CLI warehouses list $PROFILE_FLAG -o json 2>/dev/null || echo "[]")

  WAREHOUSE_COUNT=$(echo "$WAREHOUSES_JSON" | python3 -c "
import sys, json
try:
    whs = json.load(sys.stdin)
    print(len(whs))
except:
    print(0)
" 2>/dev/null)

  if [ "$WAREHOUSE_COUNT" -eq 0 ]; then
    fail "No SQL Warehouses found in your workspace.
    Create one: Workspace sidebar > SQL Warehouses > Create warehouse
    Then re-run this script."
  fi

  # Display warehouse list
  echo "$WAREHOUSES_JSON" | python3 -c "
import sys, json
whs = json.load(sys.stdin)
print()
print('  #   Name                                      Type          Size    State')
print('  ─── ───────────────────────────────────────── ──────────── ─────── ─────────')
for i, wh in enumerate(whs, 1):
    name = wh.get('name', '?')[:42]
    wtype = wh.get('warehouse_type', '?')
    if wtype == 'PRO': wtype = 'Pro'
    elif wtype == 'CLASSIC': wtype = 'Classic'
    else: wtype = 'Serverless'
    size = wh.get('cluster_size', '?')
    state = wh.get('state', '?')
    print(f'  {i:<3} {name:<43} {wtype:<12} {size:<7} {state}')
print()
"

  if [ -n "$WAREHOUSE_NAME" ]; then
    # Match by name
    WAREHOUSE_ID=$(echo "$WAREHOUSES_JSON" | python3 -c "
import sys, json
whs = json.load(sys.stdin)
target = '$WAREHOUSE_NAME'.lower()
for wh in whs:
    if wh.get('name','').lower() == target:
        print(wh['id'])
        break
" 2>/dev/null)
    if [ -z "$WAREHOUSE_ID" ]; then
      fail "No warehouse found matching name: '$WAREHOUSE_NAME'"
    fi
    success "Selected warehouse: $WAREHOUSE_NAME ($WAREHOUSE_ID)"
  else
    # Interactive selection
    read -rp "  Select warehouse number [1]: " WH_CHOICE
    WH_CHOICE="${WH_CHOICE:-1}"

    WAREHOUSE_ID=$(echo "$WAREHOUSES_JSON" | python3 -c "
import sys, json
whs = json.load(sys.stdin)
idx = int('$WH_CHOICE') - 1
if 0 <= idx < len(whs):
    print(whs[idx]['id'])
else:
    print('')
" 2>/dev/null)

    WAREHOUSE_NAME=$(echo "$WAREHOUSES_JSON" | python3 -c "
import sys, json
whs = json.load(sys.stdin)
idx = int('$WH_CHOICE') - 1
if 0 <= idx < len(whs):
    print(whs[idx].get('name',''))
" 2>/dev/null)

    if [ -z "$WAREHOUSE_ID" ]; then
      fail "Invalid selection. Please enter a number from the list."
    fi
    success "Selected: $WAREHOUSE_NAME ($WAREHOUSE_ID)"
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 4: Configure Catalog
# ══════════════════════════════════════════════════════════════════════════════
if [ -z "$QUEST_CATALOG" ]; then
  echo ""
  info "Choose a Unity Catalog name for Quest data."
  info "The scoring pipeline will create this catalog automatically if it doesn't exist."
  info "If your workspace requires a storage location for new catalogs, create it first in the UI."
  echo ""
  read -rp "  Catalog name [quest_data]: " QUEST_CATALOG
  QUEST_CATALOG="${QUEST_CATALOG:-quest_data}"
fi
success "Catalog: $QUEST_CATALOG (schema: $QUEST_SCHEMA)"

# ══════════════════════════════════════════════════════════════════════════════
# STEP 5: Build Frontend
# ══════════════════════════════════════════════════════════════════════════════
step "Step 4/8: Building frontend"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
STATIC_DIR="$SCRIPT_DIR/app/static"

if [ -f "$STATIC_DIR/index.html" ] && [ -z "$SKIP_BUILD" ]; then
  # Pre-built static files exist in the repo — only rebuild if Node.js is available
  if command -v node &>/dev/null && command -v npm &>/dev/null; then
    info "Rebuilding frontend (pre-built files exist, but refreshing)..."
    (cd "$FRONTEND_DIR" && \
      (npm install --registry https://registry.npmjs.org/ --silent 2>/dev/null || \
       npm install --registry https://npm-proxy.dev.databricks.com/ --silent 2>/dev/null || \
       npm install --silent 2>/dev/null) && \
      npm run build 2>&1 | tail -3) || true
    success "Frontend built to app/static/"
  else
    success "Using pre-built frontend from repo (Node.js not required)"
  fi
elif [ -f "$STATIC_DIR/index.html" ]; then
  success "Skipping build (--skip-build). Using existing app/static/."
else
  # No pre-built files — Node.js is required
  if ! command -v node &>/dev/null; then
    fail "No pre-built frontend found and Node.js is not installed.
    Install Node.js 18+: brew install node (macOS) or https://nodejs.org/"
  fi
  if ! command -v npm &>/dev/null; then
    fail "npm not found. It should come with Node.js."
  fi

  info "Installing dependencies..."
  # Try public npm registry first, fall back to Databricks internal proxy
  if (cd "$FRONTEND_DIR" && npm install --registry https://registry.npmjs.org/ --silent 2>&1 | tail -1); then
    success "Dependencies installed (public registry)"
  elif (cd "$FRONTEND_DIR" && npm install --registry https://npm-proxy.dev.databricks.com/ --silent 2>&1 | tail -1); then
    success "Dependencies installed (internal registry)"
  else
    fail "npm install failed. Check your network connection and npm configuration."
  fi

  info "Building React app..."
  (cd "$FRONTEND_DIR" && npm run build 2>&1 | tail -3)

  if [ ! -f "$STATIC_DIR/index.html" ]; then
    fail "Build failed — app/static/index.html not found."
  fi
  success "Frontend built to app/static/"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 5: Deploy App
# ══════════════════════════════════════════════════════════════════════════════
step "Step 5/8: Deploying to Databricks"

BUNDLE_FILE="$SCRIPT_DIR/databricks.yml"

# Child apps skip local Lakebase provisioning (which is where master/standalone
# write app.yaml), so write the federation app.yaml now — before the app source
# is uploaded by the bundle/Apps deploy.
if [ "$QUEST_ROLE" = "child" ]; then
  info "Writing child app.yaml (role=child, points at master Lakebase)..."
  write_app_yaml "$MASTER_LAKEBASE_HOST" "$LAKEBASE_DB"
fi

if [ "$DEPLOY_MODE" = "quick" ]; then
  # ── Quick Deploy: Direct API (like Forge) ──────────────────────────────────
  info "Creating app via Apps API..."

  # Create the app (ignore error if already exists)
  $CLI apps create "$APP_NAME" \
    --description "Databricks Quest - Gamification app for platform adoption" \
    $PROFILE_FLAG 2>/dev/null || true

  # Upload app source code to workspace
  APP_WORKSPACE_PATH="/Workspace/Users/${USER_EMAIL}/databricks-quest/app"
  info "Uploading app source to $APP_WORKSPACE_PATH..."
  $CLI workspace import-dir "$SCRIPT_DIR/app" "$APP_WORKSPACE_PATH" \
    --overwrite $PROFILE_FLAG 2>&1 || true

  # Upload scoring notebook
  NB_WORKSPACE_PATH="/Workspace/Users/${USER_EMAIL}/databricks-quest/notebooks"
  info "Uploading scoring notebook..."
  $CLI workspace import-dir "$SCRIPT_DIR/notebooks" "$NB_WORKSPACE_PATH" \
    --overwrite $PROFILE_FLAG 2>&1 || true

  # Start app compute
  $CLI apps start "$APP_NAME" $PROFILE_FLAG 2>/dev/null || true

  # Wait for compute
  for i in $(seq 1 30); do
    COMPUTE_STATE=$($CLI apps get "$APP_NAME" $PROFILE_FLAG -o json 2>/dev/null | python3 -c "
import sys, json
try: print(json.load(sys.stdin).get('compute_status', {}).get('state', 'UNKNOWN'))
except: print('UNKNOWN')
" 2>/dev/null || echo "UNKNOWN")
    if [ "$COMPUTE_STATE" = "ACTIVE" ]; then break; fi
    sleep 5
  done

  # Deploy source code to the app
  DEPLOY_RESULT=$($CLI apps deploy "$APP_NAME" \
    --source-code-path "$APP_WORKSPACE_PATH" \
    $PROFILE_FLAG -o json 2>/dev/null || true)

  DEPLOY_STATE=$(echo "$DEPLOY_RESULT" | python3 -c "
import sys, json
try: print(json.load(sys.stdin).get('status', {}).get('state', 'PENDING'))
except: print('PENDING')
" 2>/dev/null || echo "PENDING")

  if [ "$DEPLOY_STATE" = "SUCCEEDED" ]; then
    success "App deployed and running"
  else
    info "App deploy status: $DEPLOY_STATE (may still be starting)"
  fi

  # Set environment variables on the app
  info "Configuring app environment..."
  $CLI apps update "$APP_NAME" \
    --json "{\"config\": {\"command\": [\"uvicorn\", \"main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8000\"], \"env\": [{\"name\": \"LAKEBASE_HOST\", \"value\": \"$LAKEBASE_HOST\"}, {\"name\": \"LAKEBASE_DB\", \"value\": \"$LAKEBASE_DB\"}]}}" \
    $PROFILE_FLAG 2>/dev/null || true

  success "Quick deploy complete"

else
  # ── Full Deploy: DAB Bundle ────────────────────────────────────────────────
  if [ ! -f "$BUNDLE_FILE" ]; then
    fail "databricks.yml not found. Are you running this from the repo root?"
  fi

  # Update workspace host in databricks.yml
  python3 -c "
import re, sys
with open('$BUNDLE_FILE', 'r') as f:
    content = f.read()
host = '$WORKSPACE_HOST'
content = re.sub(r'(host:\s*)https?://[^\s]+', r'\1' + host, content)
with open('$BUNDLE_FILE', 'w') as f:
    f.write(content)
print('OK')
" || fail "Failed to update databricks.yml"

  # Update app name if customized
  if [ "$APP_NAME" != "databricks-quest" ]; then
    python3 -c "
with open('$BUNDLE_FILE', 'r') as f:
    content = f.read()
content = content.replace('databricks-quest', '$APP_NAME')
with open('$BUNDLE_FILE', 'w') as f:
    f.write(content)
print('OK')
" || warn "Could not update app name in databricks.yml"
  fi

  info "Deploying bundle (app + scoring job + notebook)..."
  set +e
  $CLI bundle deploy --target "$TARGET" $PROFILE_FLAG \
    --var "warehouse_id=$WAREHOUSE_ID" \
    --var "quest_catalog=$QUEST_CATALOG" \
    --var "quest_schema=$QUEST_SCHEMA" \
    --var "lakebase_host=$LAKEBASE_HOST" \
    --var "lakebase_db=$LAKEBASE_DB"
  DEPLOY_EXIT=$?
  set -e
  if [ "$DEPLOY_EXIT" -ne 0 ]; then
    fail "Bundle deploy failed (exit $DEPLOY_EXIT). Check the error above."
  fi

  success "Bundle deployed"
fi

# Start app compute and deploy source code (full deploy mode only)
# The bundle creates the app resource via Terraform but doesn't trigger an app deployment.
if [ "$DEPLOY_MODE" != "quick" ]; then
info "Starting app and deploying source code..."

# Determine the source code path the bundle uploaded to
BUNDLE_USER_PATH="/Workspace/Users/${USER_EMAIL}/.bundle/${APP_NAME}/${TARGET}/files/app"

# Start app compute (may already be running)
$CLI apps start "$APP_NAME" $PROFILE_FLAG -o json 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    cs = d.get('compute_status', {})
    print(f\"  Compute: {cs.get('state', '?')}\")
except: pass
" 2>/dev/null || true

# Wait for compute to be active
for i in $(seq 1 30); do
  COMPUTE_STATE=$($CLI apps get "$APP_NAME" $PROFILE_FLAG -o json 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('compute_status', {}).get('state', 'UNKNOWN'))
except: print('UNKNOWN')
" 2>/dev/null || echo "UNKNOWN")
  if [ "$COMPUTE_STATE" = "ACTIVE" ]; then
    break
  fi
  sleep 5
done

# Deploy source code
DEPLOY_RESULT=$($CLI apps deploy "$APP_NAME" \
  --source-code-path "$BUNDLE_USER_PATH" \
  $PROFILE_FLAG -o json 2>/dev/null || true)

if echo "$DEPLOY_RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('status',{}).get('state') == 'SUCCEEDED'" 2>/dev/null; then
  success "App deployed and running"
else
  DEPLOY_STATE=$(echo "$DEPLOY_RESULT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    state = d.get('status', {}).get('state', 'UNKNOWN')
    msg = d.get('status', {}).get('message', '')
    print(f'{state}: {msg}')
except: print('PENDING')
" 2>/dev/null || echo "PENDING")
  info "App deploy status: $DEPLOY_STATE (may still be starting)"
fi

fi  # end full deploy app start/deploy

# ══════════════════════════════════════════════════════════════════════════════
# STEP 6b: Provision Lakebase
# ══════════════════════════════════════════════════════════════════════════════
if [ -z "$LAKEBASE_HOST" ]; then
  step "Step 6/8: Provisioning Lakebase"

  LB_PROJECT_ID=$(echo "$APP_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g')

  # Check if Lakebase project already exists
  EXISTING_PROJECT=$($CLI postgres get-project "projects/$LB_PROJECT_ID" $PROFILE_FLAG -o json 2>/dev/null || true)
  if echo "$EXISTING_PROJECT" | python3 -c "import sys,json; json.load(sys.stdin)" &>/dev/null 2>&1; then
    info "Lakebase project '$LB_PROJECT_ID' already exists"
  else
    info "Creating Lakebase project '$LB_PROJECT_ID'..."
    $CLI postgres create-project "$LB_PROJECT_ID" \
      --json "{\"spec\": {\"display_name\": \"Databricks Quest\"}}" \
      --no-wait \
      $PROFILE_FLAG 2>&1 || true
  fi

  # Wait for endpoint to be ACTIVE
  info "Waiting for Lakebase endpoint to be ready..."
  for i in $(seq 1 60); do
    EP_STATE=$($CLI postgres list-endpoints "projects/$LB_PROJECT_ID/branches/production" \
      $PROFILE_FLAG -o json 2>/dev/null | python3 -c "
import sys, json
try:
    eps = json.load(sys.stdin)
    print(eps[0].get('status', {}).get('current_state', 'UNKNOWN'))
except: print('PENDING')
" 2>/dev/null || echo "PENDING")
    if [ "$EP_STATE" = "ACTIVE" ]; then
      break
    fi
    sleep 5
  done

  if [ "$EP_STATE" != "ACTIVE" ]; then
    fail "Lakebase endpoint did not become ACTIVE (state: $EP_STATE). Check your workspace."
  fi

  # Get the endpoint host
  LAKEBASE_HOST=$($CLI postgres list-endpoints "projects/$LB_PROJECT_ID/branches/production" \
    $PROFILE_FLAG -o json 2>/dev/null | python3 -c "
import sys, json
eps = json.load(sys.stdin)
print(eps[0]['status']['hosts']['host'])
" 2>/dev/null)

  success "Lakebase endpoint: $LAKEBASE_HOST"

  # Generate credentials and create database + tables
  info "Setting up Lakebase database and tables..."

  LB_TOKEN=$($CLI postgres generate-database-credential \
    "projects/$LB_PROJECT_ID/branches/production/endpoints/primary" \
    $PROFILE_FLAG -o json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

  # Create database (ignore error if it already exists)
  PGPASSWORD="$LB_TOKEN" psql "host=$LAKEBASE_HOST port=5432 dbname=postgres user=$USER_EMAIL sslmode=require" \
    -c "CREATE DATABASE $LAKEBASE_DB;" 2>/dev/null || true

  # Create tables (idempotent with IF NOT EXISTS)
  PGPASSWORD="$LB_TOKEN" psql "host=$LAKEBASE_HOST port=5432 dbname=$LAKEBASE_DB user=$USER_EMAIL sslmode=require" -c "
CREATE TABLE IF NOT EXISTS mission_completions (
  user_id TEXT, mission_id TEXT, mission_name TEXT, points_awarded INT,
  completed_at TIMESTAMP, period_start DATE, period_end DATE, scored_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS user_points_fact (
  user_id TEXT, event_type TEXT, mission_id TEXT, points INT,
  reason TEXT, event_timestamp TIMESTAMP, scored_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS user_profile_snapshot (
  user_id TEXT, display_name TEXT, total_points INT, level TEXT,
  current_streak INT, max_streak INT, badge_count INT, missions_completed INT,
  first_activity_date DATE, last_activity_date DATE, distinct_products_used INT,
  updated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS leaderboard (
  user_id TEXT, display_name TEXT, total_points INT, weekly_points INT,
  monthly_points INT, level TEXT, all_time_rank INT, weekly_rank INT,
  monthly_rank INT, updated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS badges (
  user_id TEXT, badge_id TEXT, badge_name TEXT, badge_icon TEXT, earned_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS notifications (
  id SERIAL PRIMARY KEY, user_id TEXT, notification_type TEXT, title TEXT,
  message TEXT, mission_id TEXT, points INT, created_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mc_user ON mission_completions(user_id);
CREATE INDEX IF NOT EXISTS idx_lb_rank ON leaderboard(all_time_rank);
CREATE INDEX IF NOT EXISTS idx_ups_user ON user_profile_snapshot(user_id);
CREATE INDEX IF NOT EXISTS idx_badges_user ON badges(user_id);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id);
" 2>/dev/null

  success "Database '$LAKEBASE_DB' and tables created"

  # Apply GameDay (Event Mode) schema migrations before granting access, so the
  # service principal GRANT below also covers the newly-created GameDay tables.
  # Skipped entirely for a legacy (Event-Mode-off) deploy so its DB is untouched.
  if [ "$QUEST_EVENT_MODE" = "on" ]; then
    run_gameday_migrations "$LAKEBASE_HOST" "$LAKEBASE_DB" "$USER_EMAIL" "$LB_TOKEN"
  else
    info "Event Mode off — skipping GameDay schema migrations (legacy adoption app)."
  fi

  # master: provision the shared event-writer role children will use.
  if [ "$QUEST_ROLE" = "master" ]; then
    EVENT_WRITER_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
    provision_event_writer_role "$LAKEBASE_HOST" "$LAKEBASE_DB" "$USER_EMAIL" "$LB_TOKEN" \
      "$MASTER_LAKEBASE_USER" "$EVENT_WRITER_PASSWORD" || true
  fi

  # Grant app service principal access to Lakebase
  info "Granting app service principal access to Lakebase..."

  SP_CLIENT_ID=$($CLI apps get "$APP_NAME" $PROFILE_FLAG -o json 2>/dev/null | python3 -c "
import sys, json
print(json.load(sys.stdin).get('service_principal_client_id', ''))
" 2>/dev/null || true)

  if [ -n "$SP_CLIENT_ID" ]; then
    # Create Postgres role for the SP (ignore error if exists)
    $CLI postgres create-role "projects/$LB_PROJECT_ID/branches/production" \
      --role-id "quest-sp" \
      --json "{\"spec\": {\"identity_type\": \"SERVICE_PRINCIPAL\", \"postgres_role\": \"$SP_CLIENT_ID\", \"auth_method\": \"LAKEBASE_OAUTH_V1\", \"membership_roles\": [\"DATABRICKS_SUPERUSER\"]}}" \
      $PROFILE_FLAG 2>/dev/null || true

    # Grant SELECT on all tables
    PGPASSWORD="$LB_TOKEN" psql "host=$LAKEBASE_HOST port=5432 dbname=$LAKEBASE_DB user=$USER_EMAIL sslmode=require" -c "
GRANT SELECT ON ALL TABLES IN SCHEMA public TO \"$SP_CLIENT_ID\";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO \"$SP_CLIENT_ID\";
" 2>/dev/null || true

    success "Service principal ($SP_CLIENT_ID) granted Lakebase access"
  else
    warn "Could not find app service principal — you may need to grant Lakebase access manually"
  fi

  # Patch app.yaml with actual Lakebase values (valueFrom doesn't work
  # when app config isn't set by the bundle's Terraform lifecycle). The helper
  # emits the unchanged two-var file for standalone and adds federation env
  # (QUEST_ROLE etc.) for master.
  info "Updating app.yaml with Lakebase endpoint..."
  write_app_yaml "$LAKEBASE_HOST" "$LAKEBASE_DB"

  # Re-deploy bundle with the Lakebase host now set
  info "Redeploying with Lakebase configuration..."
  $CLI bundle deploy --target "$TARGET" $PROFILE_FLAG \
    --var "warehouse_id=$WAREHOUSE_ID" \
    --var "quest_catalog=$QUEST_CATALOG" \
    --var "quest_schema=$QUEST_SCHEMA" \
    --var "lakebase_host=$LAKEBASE_HOST" \
    --var "lakebase_db=$LAKEBASE_DB" 2>&1 || true

  # Re-deploy app source code with updated app.yaml
  $CLI apps deploy "$APP_NAME" \
    --source-code-path "$BUNDLE_USER_PATH" \
    $PROFILE_FLAG -o json 2>/dev/null || true

  success "App updated with Lakebase configuration"
elif [ "$QUEST_ROLE" = "child" ]; then
  step "Step 6/8: Lakebase (child → master)"
  success "Child uses MASTER shared Lakebase: $LAKEBASE_HOST/$LAKEBASE_DB"
  info "Skipping local Lakebase provisioning and migrations (master owns the schema)."
  # app.yaml was already written for the child before the deploy step. Nothing
  # to provision here — the child only connects with the shared writer credential.
else
  step "Step 6/8: Lakebase"
  success "Using provided Lakebase: $LAKEBASE_HOST/$LAKEBASE_DB"

  # Apply GameDay migrations against the provided Lakebase endpoint (Event Mode
  # only). Mint a database credential if we can; otherwise fall back to the
  # workspace token. A legacy deploy skips this and leaves the DB untouched.
  LB_PROJECT_ID=$(echo "$APP_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g')
  LB_TOKEN=$($CLI postgres generate-database-credential \
    "projects/$LB_PROJECT_ID/branches/production/endpoints/primary" \
    $PROFILE_FLAG -o json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])" 2>/dev/null || true)
  if [ -z "$LB_TOKEN" ]; then
    LB_TOKEN="${DATABRICKS_TOKEN:-}"
  fi
  if [ "$QUEST_EVENT_MODE" = "on" ]; then
    run_gameday_migrations "$LAKEBASE_HOST" "$LAKEBASE_DB" "$USER_EMAIL" "$LB_TOKEN"
  else
    info "Event Mode off — skipping GameDay schema migrations (legacy adoption app)."
  fi

  # master with a pre-provided Lakebase: provision the shared event-writer role
  # and refresh app.yaml with master federation env.
  if [ "$QUEST_ROLE" = "master" ]; then
    EVENT_WRITER_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
    provision_event_writer_role "$LAKEBASE_HOST" "$LAKEBASE_DB" "$USER_EMAIL" "$LB_TOKEN" \
      "$MASTER_LAKEBASE_USER" "$EVENT_WRITER_PASSWORD" || true
    write_app_yaml "$LAKEBASE_HOST" "$LAKEBASE_DB"
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 7: Run Scoring Pipeline
# ══════════════════════════════════════════════════════════════════════════════
step "Step 7/8: Running scoring pipeline"

if [ -n "$SKIP_SCORING" ]; then
  warn "Skipping scoring pipeline (--skip-scoring)."
  if [ "$DEPLOY_MODE" = "quick" ]; then
    warn "Run the scoring notebook manually from your workspace."
  else
    warn "Run it manually later: databricks bundle run quest_scoring_pipeline --target $TARGET"
  fi
elif [ "$DEPLOY_MODE" = "quick" ]; then
  info "Running scoring notebook via one-time job..."
  info "Takes 2-5 minutes on first run..."
  echo ""

  NB_WORKSPACE_PATH="/Workspace/Users/${USER_EMAIL}/databricks-quest/notebooks/scoring_pipeline"
  set +e
  $CLI jobs submit \
    --json "{\"run_name\": \"Quest Scoring (one-time)\", \"tasks\": [{\"task_key\": \"run_scoring\", \"notebook_task\": {\"notebook_path\": \"$NB_WORKSPACE_PATH\", \"base_parameters\": {\"quest_catalog\": \"$QUEST_CATALOG\", \"quest_schema\": \"$QUEST_SCHEMA\", \"lakebase_host\": \"$LAKEBASE_HOST\", \"lakebase_db\": \"$LAKEBASE_DB\", \"app_name\": \"$APP_NAME\", \"warehouse_id\": \"$WAREHOUSE_ID\"}, \"source\": \"WORKSPACE\"}, \"environment_key\": \"default\"}], \"environments\": [{\"environment_key\": \"default\", \"spec\": {\"client\": \"1\", \"dependencies\": [\"psycopg2-binary\"]}}]}" \
    $PROFILE_FLAG 2>&1
  RUN_EXIT=$?
  set -e
  if [ "$RUN_EXIT" -ne 0 ]; then
    warn "Scoring pipeline submit failed (exit $RUN_EXIT). Run the notebook manually from your workspace."
  else
    success "Scoring pipeline submitted"
  fi
else
  info "This reads system tables, scores missions, creates tables, and grants permissions."
  info "Takes 2-5 minutes on first run..."
  echo ""

  set +e
  $CLI bundle run quest_scoring_pipeline --target "$TARGET" $PROFILE_FLAG \
    --var "warehouse_id=$WAREHOUSE_ID" \
    --var "quest_catalog=$QUEST_CATALOG" \
    --var "quest_schema=$QUEST_SCHEMA" \
    --var "lakebase_host=" \
    --var "lakebase_db="
  RUN_EXIT=$?
  set -e
  if [ "$RUN_EXIT" -ne 0 ]; then
    warn "Scoring pipeline failed (exit $RUN_EXIT). You can re-run it manually later."
  fi

  success "Scoring pipeline complete"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 7b: Sync Delta tables to Lakebase
# ══════════════════════════════════════════════════════════════════════════════
if [ -n "$LAKEBASE_HOST" ] && [ -z "$SKIP_SCORING" ]; then
  step "Syncing data to Lakebase"

  info "Reading scored data from Delta tables and syncing to Lakebase..."

  # Generate fresh Lakebase credential
  LB_PROJECT_ID=$(echo "$APP_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g')
  LB_TOKEN=$($CLI postgres generate-database-credential \
    "projects/$LB_PROJECT_ID/branches/production/endpoints/primary" \
    $PROFILE_FLAG -o json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])" 2>/dev/null || true)

  if [ -z "$LB_TOKEN" ]; then
    # Fallback: use workspace auth token
    LB_TOKEN=$($CLI auth token $PROFILE_FLAG -o json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || echo "$DATABRICKS_TOKEN")
  fi

  # Sync each table: read from Delta via SQL Statements API, write to Lakebase via psql
  python3 << PYEOF
import urllib.request, json, os, time, subprocess

HOST = os.environ.get("DATABRICKS_HOST", "$WORKSPACE_HOST")
TOKEN = os.environ.get("DATABRICKS_TOKEN", "")
WH_ID = "$WAREHOUSE_ID"
CATALOG = "$QUEST_CATALOG"
SCHEMA = "$QUEST_SCHEMA"
LB_HOST = "$LAKEBASE_HOST"
LB_DB = "$LAKEBASE_DB"
LB_TOKEN = "$LB_TOKEN"
USER_EMAIL = "$USER_EMAIL"

TABLES = [
    ("mission_completions", "user_id, mission_id, mission_name, points_awarded, completed_at, period_start, period_end, scored_at"),
    ("user_points_fact", "user_id, event_type, mission_id, points, reason, event_timestamp, scored_at"),
    ("user_profile_snapshot", "user_id, display_name, total_points, level, current_streak, max_streak, badge_count, missions_completed, first_activity_date, last_activity_date, distinct_products_used, updated_at"),
    ("leaderboard", "user_id, display_name, total_points, weekly_points, monthly_points, level, all_time_rank, weekly_rank, monthly_rank, updated_at"),
    ("badges", "user_id, badge_id, badge_name, badge_icon, earned_at"),
    ("notifications", "user_id, notification_type, title, message, mission_id, points, created_at"),
]

def run_sql(sql, timeout=50):
    """Execute SQL via Statements API and return rows."""
    data = json.dumps({"warehouse_id": WH_ID, "statement": sql, "wait_timeout": f"{timeout}s"}).encode()
    req = urllib.request.Request(
        f"{HOST}/api/2.0/sql/statements", data=data,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=60)
    result = json.loads(resp.read())
    status = result.get("status", {}).get("state", "?")
    if status == "SUCCEEDED":
        return result.get("result", {}).get("data_array", [])
    elif status == "PENDING" or status == "RUNNING":
        # Poll
        stmt_id = result.get("statement_id", "")
        for _ in range(30):
            time.sleep(2)
            req2 = urllib.request.Request(
                f"{HOST}/api/2.0/sql/statements/{stmt_id}",
                headers={"Authorization": f"Bearer {TOKEN}"})
            resp2 = urllib.request.urlopen(req2, timeout=30)
            result2 = json.loads(resp2.read())
            if result2.get("status", {}).get("state") == "SUCCEEDED":
                return result2.get("result", {}).get("data_array", [])
            elif result2.get("status", {}).get("state") == "FAILED":
                print(f"  SQL failed: {result2.get('status', {}).get('error', {}).get('message', '?')[:200]}")
                return []
        return []
    else:
        msg = result.get("status", {}).get("error", {}).get("message", "?")[:200]
        print(f"  SQL error: {msg}")
        return []

def psql_exec(sql):
    """Execute SQL against Lakebase via psql."""
    env = os.environ.copy()
    env["PGPASSWORD"] = LB_TOKEN
    result = subprocess.run(
        ["psql", f"host={LB_HOST} port=5432 dbname={LB_DB} user={USER_EMAIL} sslmode=require",
         "-c", sql],
        capture_output=True, text=True, env=env, timeout=60)
    if result.returncode != 0:
        print(f"  psql error: {result.stderr[:200]}")
    return result.returncode == 0

synced = 0
for table_name, columns in TABLES:
    print(f"  Syncing {table_name}...")
    cols = [c.strip() for c in columns.split(",")]
    rows = run_sql(f"SELECT {columns} FROM \`{CATALOG}\`.\`{SCHEMA}\`.\`{table_name}\`")
    if rows is None:
        rows = []

    # Delete existing data
    psql_exec(f"DELETE FROM {table_name}")

    if rows:
        # Build INSERT statements in batches
        batch_size = 100
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i+batch_size]
            values = []
            for row in batch:
                escaped = []
                for v in row:
                    if v is None or v == "null":
                        escaped.append("NULL")
                    else:
                        escaped.append("'" + str(v).replace("'", "''") + "'")
                values.append(f"({', '.join(escaped)})")
            insert_sql = f"INSERT INTO {table_name} ({columns}) VALUES {', '.join(values)}"
            psql_exec(insert_sql)

    print(f"  {table_name}: {len(rows)} rows synced")
    synced += 1

print(f"Lakebase sync complete: {synced}/{len(TABLES)} tables synced")
PYEOF

  success "Lakebase sync complete"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 8: Get App URL & Print Success
# ══════════════════════════════════════════════════════════════════════════════
step "Step 8/8: Verifying deployment"

# Wait a moment for the app to be registered
sleep 2

# Try to get the app info (dev mode prepends [dev deep_basu] to the name)
DEV_APP_NAME="$APP_NAME"
APP_JSON=$($CLI apps get "$DEV_APP_NAME" $PROFILE_FLAG -o json 2>/dev/null || true)

if [ -z "$APP_JSON" ] || ! echo "$APP_JSON" | python3 -c "import sys,json; json.load(sys.stdin)" &>/dev/null; then
  # In dev mode, the bundle may prefix the app name
  DEV_APP_NAME="[dev ${USER_EMAIL%%@*}] $APP_NAME"
  APP_JSON=$($CLI apps get "$DEV_APP_NAME" $PROFILE_FLAG -o json 2>/dev/null || true)
fi

if [ -z "$APP_JSON" ] || ! echo "$APP_JSON" | python3 -c "import sys,json; json.load(sys.stdin)" &>/dev/null; then
  # Try without the brackets format (bundle dev mode uses different naming)
  APP_JSON=""
fi

APP_URL=""
APP_STATE=""
if [ -n "$APP_JSON" ]; then
  APP_URL=$(echo "$APP_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('url',''))" 2>/dev/null || true)
  APP_STATE=$(echo "$APP_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',{}).get('state','UNKNOWN'))" 2>/dev/null || true)
fi

# ── Success Banner ───────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${GREEN}║        Databricks Quest — Deployed!          ║${NC}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}Workspace:${NC}     $WORKSPACE_HOST"
echo -e "  ${BOLD}Catalog:${NC}       $QUEST_CATALOG.$QUEST_SCHEMA"
echo -e "  ${BOLD}Warehouse:${NC}     ${WAREHOUSE_NAME:-$WAREHOUSE_ID}"
echo -e "  ${BOLD}App Name:${NC}      $APP_NAME"
if [ -n "$APP_URL" ]; then
  echo -e "  ${BOLD}App URL:${NC}       ${CYAN}$APP_URL${NC}"
fi
if [ -n "$APP_STATE" ]; then
  echo -e "  ${BOLD}App State:${NC}     $APP_STATE"
fi
echo -e "  ${BOLD}Lakebase:${NC}      $LAKEBASE_HOST/$LAKEBASE_DB"
if [ "$QUEST_EVENT_MODE" = "on" ]; then
  echo -e "  ${BOLD}Event Mode:${NC}    ${GREEN}ENABLED${NC} (GameDay)"
else
  echo -e "  ${BOLD}Event Mode:${NC}    off (legacy adoption app)"
fi
if [ -n "$QUEST_ADMIN_ALLOWLIST" ]; then
  echo -e "  ${BOLD}Admins:${NC}        $QUEST_ADMIN_ALLOWLIST ${DIM}(seeded to quest_admins; manage more in-app)${NC}"
elif [ "$QUEST_ROLE" = "child" ]; then
  echo -e "  ${BOLD}Admins:${NC}        inherited from master (shared quest_admins)"
else
  echo -e "  ${BOLD}Admins:${NC}        ${YELLOW}open${NC} (no allowlist — Admin page visible to all)"
fi
if [ "$QUEST_ROLE" != "standalone" ]; then
  echo -e "  ${BOLD}Role:${NC}          $QUEST_ROLE${EVENT_SLUG:+  (event: $EVENT_SLUG)}"
  [ -n "$WORKSPACE_ID" ] && echo -e "  ${BOLD}Workspace ID:${NC}  $WORKSPACE_ID"
fi
echo ""

# master: print the shared event-writer credential to hand to child deploys.
if [ "$QUEST_ROLE" = "master" ] && [ -n "$EVENT_WRITER_PASSWORD" ]; then
  echo -e "${BOLD}${YELLOW}── Shared event-writer credential (give to child workspaces) ──${NC}"
  echo ""
  echo -e "  Children deploy with these flags (rotate per event):"
  echo ""
  echo -e "    ${CYAN}./deploy.sh \\\\${NC}"
  echo -e "    ${CYAN}  --role child \\\\${NC}"
  echo -e "    ${CYAN}  --event ${EVENT_SLUG:-<event-slug>} \\\\${NC}"
  echo -e "    ${CYAN}  --master-lakebase-host $LAKEBASE_HOST \\\\${NC}"
  echo -e "    ${CYAN}  --master-lakebase-user $MASTER_LAKEBASE_USER \\\\${NC}"
  echo -e "    ${CYAN}  --master-lakebase-token '$EVENT_WRITER_PASSWORD'${NC}"
  echo ""
  echo -e "  ${DIM}Verify connectivity from a child first:${NC}"
  echo -e "  ${DIM}  PGPASSWORD='$EVENT_WRITER_PASSWORD' python3 scripts/federation_spike.py \\\\${NC}"
  echo -e "  ${DIM}    --host $LAKEBASE_HOST --db $LAKEBASE_DB --user $MASTER_LAKEBASE_USER${NC}"
  echo ""
  warn "Store this secret securely — it is shown once and is not persisted by this script."
  echo ""
fi

if [ -n "$APP_URL" ]; then
  echo -e "  Open the app: ${BOLD}${CYAN}$APP_URL${NC}"
else
  echo -e "  Get the app URL:"
  echo -e "    ${DIM}databricks apps get $APP_NAME $PROFILE_FLAG${NC}"
fi

echo ""
echo -e "  ${DIM}The scoring pipeline is scheduled to run every 4 hours.${NC}"
echo -e "  ${DIM}To re-run it manually:${NC}"
echo -e "  ${DIM}  databricks bundle run quest_scoring_pipeline --target $TARGET \\${NC}"
echo -e "  ${DIM}    --var warehouse_id=$WAREHOUSE_ID \\${NC}"
echo -e "  ${DIM}    --var quest_catalog=$QUEST_CATALOG${NC}"
echo ""
