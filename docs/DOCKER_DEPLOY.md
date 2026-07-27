# Deploy Quest with Docker

The simplest way to deploy Quest: one `docker run`, no local tools to install.
Everything the deploy needs (Databricks CLI, psql, Terraform, the prebuilt
frontend) is baked into the image. You only need Docker.

---

## What this does (and does not do)

- It runs Quest's standard `deploy.sh` inside a container and deploys the app +
  scoring job into your Databricks workspace.
- The Quest app runs on **Databricks Apps** (source-code runtime), exactly as a
  normal deploy -- it does NOT run inside the container. The container is a
  deploy tool that exits when the deploy finishes.
- The scoring job runs in Databricks on its 4-hourly schedule, reading system
  tables. It also does not run in the container.

---

## Prerequisites

- Docker (Desktop, Colima, or any Docker engine).
- A Databricks **personal access token** for the workspace you are deploying to.
  Create one in your workspace under User Settings > Developer > Access tokens.

---

## Build

```bash
git clone https://github.com/deepbasu123/databricks-quest.git
cd databricks-quest
docker build -t databricks-quest .
```

The image is around 537 MB. It builds for your host architecture automatically
(Apple Silicon and Intel both work, with both the legacy Docker builder and
BuildKit).

---

## Deploy

```bash
docker run --rm \
  -e DATABRICKS_HOST=https://my-workspace.cloud.databricks.com \
  -e DATABRICKS_TOKEN=dapiXXXXXXXXXXXXXXXX \
  databricks-quest --catalog my_catalog
```

The token is passed at run time via `-e` and is never stored in the image.

**Default behavior (Full Deploy):** unless you pass `--quick`, the deploy
provisions the app, uploads and registers the scoring notebook, and creates a
scheduled job that re-scores every 4 hours. It also provisions a Lakebase
Postgres instance named after the app and grants the app's service principal
access automatically.

**Warehouse auto-provisioning:** if you do not pass `--warehouse`, the deploy
creates a dedicated 2X-Small serverless SQL warehouse named
`<app-name>-warehouse` with a 60-minute auto-stop. A warehouse with that name
will appear in your workspace after the deploy. To use an existing warehouse
instead, pass `--warehouse "My Warehouse Name"`.

---

## Flags

Any `deploy.sh` flag works after the image name:

| Flag | What it does |
|------|-------------|
| `--catalog NAME` | Unity Catalog for Quest data (required) |
| `--schema NAME` | Schema name for Quest tables (default: `quest`) |
| `--app-name NAME` | App name (default: `databricks-quest`) |
| `--warehouse "NAME"` | Use this existing SQL warehouse instead of auto-provisioning one |
| `--quick` | App-only deploy via direct API, no DAB bundle, no recurring job |
| `--skip-scoring` | Deploy everything but do not run the scoring job now (it still runs on schedule) |
| `--event-mode` | Enable GameDay / Event Mode (off by default) |

The image always uses the committed prebuilt frontend (`--skip-build` is
forced). Node is not installed in the image.

---

## Deploy to multiple workspaces

Re-run with different `DATABRICKS_HOST` and `DATABRICKS_TOKEN` values. No
rebuild needed.

```bash
# Workspace 1
docker run --rm \
  -e DATABRICKS_HOST=https://workspace-1.cloud.databricks.com \
  -e DATABRICKS_TOKEN=dapi111... \
  databricks-quest --catalog my_catalog

# Workspace 2
docker run --rm \
  -e DATABRICKS_HOST=https://workspace-2.cloud.databricks.com \
  -e DATABRICKS_TOKEN=dapi222... \
  databricks-quest --catalog my_catalog
```

---

## Troubleshooting

**`Missing required environment variable(s): DATABRICKS_HOST` (or
`DATABRICKS_TOKEN`)** -- you forgot to pass `-e DATABRICKS_HOST=...` or
`-e DATABRICKS_TOKEN=...` to `docker run`. Both are required.

**A warehouse named `<app-name>-warehouse` appeared in your workspace** -- this
is expected. The deploy auto-provisions it when `--warehouse` is not specified.
If you want to use an existing warehouse, pass `--warehouse "My Warehouse Name"`.

**Auth errors inside the container** -- confirm your token is valid and has not
expired. Tokens can be revoked or expire based on your workspace admin policy.
Regenerate one from User Settings > Developer > Access tokens.
