#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Databricks Quest — Docker entrypoint
#
# Validates env-var PAT auth, then runs the standard deploy.sh with every tool
# (Databricks CLI, psql, Terraform, prebuilt frontend) already baked into the
# image. All deploy.sh flags pass straight through.
#
# Usage:
#   docker run --rm \
#     -e DATABRICKS_HOST=https://my-workspace.cloud.databricks.com \
#     -e DATABRICKS_TOKEN=dapiXXXX \
#     databricks-quest --catalog my_catalog [more deploy.sh flags]
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

RED=$'\033[0;31m'; NC=$'\033[0m'
err() { echo "${RED}✗${NC} $*" >&2; }

missing=""
[ -z "${DATABRICKS_HOST:-}" ]  && missing="DATABRICKS_HOST"
[ -z "${DATABRICKS_TOKEN:-}" ] && missing="${missing:+$missing, }DATABRICKS_TOKEN"
if [ -n "$missing" ]; then
  err "Missing required environment variable(s): $missing"
  cat >&2 <<'USAGE'

  Run this image with your workspace URL and a personal access token:

    docker run --rm \
      -e DATABRICKS_HOST=https://my-workspace.cloud.databricks.com \
      -e DATABRICKS_TOKEN=dapiXXXXXXXXXXXXXXXX \
      databricks-quest --catalog my_catalog

  Create a token in your workspace: User Settings → Developer → Access tokens.
  Any deploy.sh flag (--catalog, --warehouse, --quick, --app-name, ...) works here.
USAGE
  exit 1
fi

cd /quest

# deploy.sh's "Choose deployment mode" prompt has no non-interactive fallback,
# and a container has no TTY — so default to Full Deploy (the recommended mode:
# app + scoring notebook + the 4-hourly scheduled job) UNLESS the caller already
# picked a mode. This keeps the container non-interactive without editing
# deploy.sh. The user can still pass --quick (or --full) explicitly.
mode_flag="--full"
for arg in "$@"; do
  case "$arg" in
    --quick|--full) mode_flag=""; break ;;
  esac
done

# --skip-build forces use of the committed app/static/ (deploy.sh resets the
# SKIP_BUILD env var at startup, so the flag is the reliable signal). Node is
# not installed in the image, so there is nothing to build anyway.
exec ./deploy.sh --skip-build $mode_flag "$@"
