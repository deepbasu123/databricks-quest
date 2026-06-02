# Databricks Quest - Deployment Guide

Deploy Databricks Quest on your workspace in about 10 minutes.

---

## Prerequisites

You need four things before you start:

| # | What | How to check / get it |
|---|------|-----------------------|
| 1 | **Databricks workspace** with Unity Catalog and system tables enabled | Log in at your workspace URL. Run `SELECT * FROM system.billing.usage LIMIT 1` in the SQL editor. If it returns a row, you're good. If not, ask your workspace admin to [enable system tables](https://docs.databricks.com/en/administration-guide/system-tables/index.html). |
| 2 | **Databricks CLI v0.285+** | Run `databricks --version`. If missing or too old: `brew install databricks/tap/databricks` on macOS, or [see docs](https://docs.databricks.com/en/dev-tools/cli/install.html). |
| 3 | **Node.js 18+** | Run `node --version`. If missing: [nodejs.org](https://nodejs.org/) or `brew install node`. |
| 4 | **psql client** | Run `psql --version`. If missing: `brew install postgresql@16` on macOS, or `apt install postgresql-client` on Linux. |

You do **not** need to:
- Create a catalog or schema in advance (the pipeline handles it)
- Find your SQL Warehouse ID (the script discovers warehouses for you)
- Edit any config files

---

## Deploy

```bash
git clone https://github.com/deepbasu123/databricks-quest.git
cd databricks-quest
./deploy.sh
```

The script walks you through everything interactively:

1. **Checks prerequisites** - Verifies CLI version and Node.js
2. **Authenticates** - Opens your browser to log in (if not already authenticated)
3. **Lists SQL Warehouses** - Shows available warehouses, you pick one by number
4. **Asks for a catalog name** - Where Quest tables will live (e.g. `quest_data`)
5. **Builds the frontend** - Runs `npm install` and `npm run build`
6. **Deploys the bundle** - Creates the Databricks App, uploads the notebook, creates a scheduled job
7. **Runs the scoring pipeline** - Creates the catalog/schema/tables, scores missions, grants permissions
8. **Prints the app URL** - Open it in your browser

The whole process takes about 10 minutes, mostly waiting for the scoring pipeline.

### Non-Interactive Deploy

If you already know your warehouse ID and catalog name:

```bash
./deploy.sh --warehouse-id a1b2c3d4e5f67890 --catalog quest_data
```

All flags:

| Flag | Description | Default |
|------|-------------|---------|
| `--warehouse NAME` | Select warehouse by name | Interactive prompt |
| `--warehouse-id ID` | Use this warehouse ID directly | Interactive prompt |
| `--catalog NAME` | Unity Catalog name for Quest data | Interactive prompt |
| `--schema NAME` | Schema name for Quest tables | `quest` |
| `--app-name NAME` | Custom app name | `databricks-quest` |
| `--profile NAME` | Databricks CLI profile to use | Default profile |
| `--target TARGET` | Bundle target (`dev` or `prod`) | `dev` |
| `--lakebase-host HOST` | Lakebase endpoint for faster reads | None (uses SQL Warehouse) |
| `--full` | Full deploy with DAB bundle + scoring job | Default |
| `--quick` | Quick deploy via Apps API (like Forge) | Interactive prompt |
| `--skip-build` | Skip frontend build (reuse existing) | Builds every time |
| `--skip-scoring` | Skip running the scoring pipeline | Runs every time |

---

## After Deployment

### Open the app

The deploy script prints the URL at the end. You can also get it anytime:

```bash
databricks apps get databricks-quest
```

Open the URL in your browser. You'll log in with your Databricks credentials.

### How data stays fresh

A scheduled job runs the scoring pipeline every 4 hours. It re-reads system tables and updates all Quest data. No action needed from you.

To run it manually:

```bash
databricks bundle run quest_scoring_pipeline --target dev \
  --var warehouse_id=YOUR_WAREHOUSE_ID \
  --var quest_catalog=YOUR_CATALOG_NAME
```

### What got deployed

| Component | Description |
|-----------|-------------|
| **Databricks App** | React + FastAPI web app at its own URL |
| **Scoring Notebook** | Uploaded to your workspace, reads system tables and scores missions |
| **Scheduled Job** | Runs the scoring notebook every 4 hours |
| **Delta Tables** | `<catalog>.quest.*` with 6 tables: mission_completions, user_profile_snapshot, leaderboard, badges, notifications, user_points_fact |

### Permissions (handled automatically)

The scoring pipeline automatically grants the app's service principal:
- `USE_CATALOG` on your Quest catalog
- `USE_SCHEMA` and `SELECT` on the Quest schema
- `CAN_USE` on the SQL Warehouse

If the auto-grant fails (some workspaces restrict this), you'll see a warning in the pipeline output. See the troubleshooting section below for manual grant commands.

---

## Optional: Lakebase Integration

By default, the app queries a SQL Warehouse. This works fine but responses take 2-5 seconds. **Lakebase** (Databricks' managed PostgreSQL) brings that down to sub-second.

Skip this section if you just want to get Quest running.

### Requirements

- Databricks CLI **v0.285.0+** (`databricks --version`)
- `psql` client installed (`brew install postgresql@16` on macOS)

### Setup

```bash
# 1. Create a Lakebase project
databricks postgres create-project databricks-quest \
  --json '{"spec": {"display_name": "Databricks Quest"}}' \
  --no-wait

# 2. Wait for it to be ready (1-2 minutes)
databricks postgres list-endpoints projects/databricks-quest/branches/production
# Repeat until current_state shows ACTIVE. Copy the host value.

# 3. Get connection details
HOST=$(databricks postgres list-endpoints projects/databricks-quest/branches/production \
  -o json | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['status']['hosts']['host'])")
TOKEN=$(databricks postgres generate-database-credential \
  projects/databricks-quest/branches/production/endpoints/primary -o json \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
EMAIL=$(databricks current-user me -o json | python3 -c "import sys,json; print(json.load(sys.stdin)['userName'])")

# 4. Create database and tables
PGPASSWORD=$TOKEN psql "host=$HOST port=5432 dbname=postgres user=$EMAIL sslmode=require" \
  -c "CREATE DATABASE quest_db;"

PGPASSWORD=$TOKEN psql "host=$HOST port=5432 dbname=quest_db user=$EMAIL sslmode=require" -c "
CREATE TABLE mission_completions (
  user_id TEXT, mission_id TEXT, mission_name TEXT, points_awarded INT,
  completed_at TIMESTAMP, period_start DATE, period_end DATE, scored_at TIMESTAMP
);
CREATE TABLE user_points_fact (
  user_id TEXT, event_type TEXT, mission_id TEXT, points INT,
  reason TEXT, event_timestamp TIMESTAMP, scored_at TIMESTAMP
);
CREATE TABLE user_profile_snapshot (
  user_id TEXT, display_name TEXT, total_points INT, level TEXT,
  current_streak INT, max_streak INT, badge_count INT, missions_completed INT,
  first_activity_date DATE, last_activity_date DATE, distinct_products_used INT,
  updated_at TIMESTAMP
);
CREATE TABLE leaderboard (
  user_id TEXT, display_name TEXT, total_points INT, weekly_points INT,
  monthly_points INT, level TEXT, all_time_rank INT, weekly_rank INT,
  monthly_rank INT, updated_at TIMESTAMP
);
CREATE TABLE badges (
  user_id TEXT, badge_id TEXT, badge_name TEXT, badge_icon TEXT, earned_at TIMESTAMP
);
CREATE TABLE notifications (
  id SERIAL PRIMARY KEY, user_id TEXT, notification_type TEXT, title TEXT,
  message TEXT, mission_id TEXT, points INT, created_at TIMESTAMP
);
CREATE INDEX idx_mc_user ON mission_completions(user_id);
CREATE INDEX idx_lb_rank ON leaderboard(all_time_rank);
CREATE INDEX idx_ups_user ON user_profile_snapshot(user_id);
CREATE INDEX idx_badges_user ON badges(user_id);
CREATE INDEX idx_notif_user ON notifications(user_id);
"

# 5. Grant the app's service principal access
SP_CLIENT_ID=$(databricks apps get databricks-quest -o json \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['service_principal_client_id'])")

databricks postgres create-role projects/databricks-quest/branches/production \
  --role-id quest-sp \
  --json "{\"spec\": {\"identity_type\": \"SERVICE_PRINCIPAL\", \"postgres_role\": \"$SP_CLIENT_ID\", \"auth_method\": \"LAKEBASE_OAUTH_V1\", \"membership_roles\": [\"DATABRICKS_SUPERUSER\"]}}"

PGPASSWORD=$TOKEN psql "host=$HOST port=5432 dbname=quest_db user=$EMAIL sslmode=require" -c "
GRANT SELECT ON ALL TABLES IN SCHEMA public TO \"$SP_CLIENT_ID\";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO \"$SP_CLIENT_ID\";
"

# 6. Redeploy with Lakebase enabled
./deploy.sh --lakebase-host $HOST --skip-scoring

# 7. Re-run the scoring pipeline to sync data to Lakebase
databricks bundle run quest_scoring_pipeline --target dev
```

---

## System Tables Used

The scoring pipeline queries these read-only tables that Databricks maintains automatically:

| Table | What It Tracks | Missions It Powers |
|-------|---------------|-------------------|
| `system.billing.usage` | All compute usage per user | First Steps, activity streaks, product breadth |
| `system.lakeflow.jobs` | Job creation and configuration | Job Creator, Scheduler |
| `system.lakeflow.job_run_timeline` | Job execution history | Consistent Operator |
| `system.lakeflow.pipelines` | Pipeline creation | Pipeline Builder, Auto Loader Pioneer |
| `system.lakeflow.pipeline_update_timeline` | Pipeline execution results | Pipeline Runner |
| `system.query.history` | SQL query execution | Data Explorer |
| `system.access.audit` | Workspace actions (create dashboard, etc.) | Genie Creator, Dashboard Designer |

---

## Troubleshooting

### "Setup Required" message in the app

The scoring pipeline hasn't run yet, or it failed. Run it manually:

```bash
databricks bundle run quest_scoring_pipeline --target dev \
  --var warehouse_id=YOUR_WAREHOUSE_ID \
  --var quest_catalog=YOUR_CATALOG_NAME
```

Check the job run output in your workspace under **Workflows** > **Job Runs**.

### "Catalog does not exist and could not be auto-created"

Some workspaces require a storage location when creating catalogs. Create the catalog manually:
1. In your workspace, go to **Catalog** in the left sidebar
2. Click **+ Add** > **Add a catalog**
3. Name it whatever you used for the catalog (e.g. `quest_data`)
4. Re-run: `./deploy.sh --skip-build`

### No users on the leaderboard

System tables can take a few hours to populate after workspace creation. The scoring pipeline only finds users who have actual compute usage recorded in `system.billing.usage`. If your workspace is brand new, wait a few hours and re-run the pipeline.

### Permission errors on system tables

Ask your workspace admin to:
1. Enable system tables (Admin Console > System Tables)
2. Grant you SELECT access:

```sql
GRANT USE_CATALOG ON CATALOG system TO `your_email@company.com`;
GRANT USE_SCHEMA ON SCHEMA system.billing TO `your_email@company.com`;
GRANT SELECT ON SCHEMA system.billing TO `your_email@company.com`;
-- Repeat for: system.lakeflow, system.query, system.access
```

### Auto-grant failed for app permissions

If the scoring pipeline couldn't grant the app service principal access, run these SQL commands manually. Find the service principal name with `databricks apps get databricks-quest`:

```sql
GRANT USE_CATALOG ON CATALOG <your_catalog> TO `<service_principal_name>`;
GRANT USE_SCHEMA ON SCHEMA <your_catalog>.quest TO `<service_principal_name>`;
GRANT SELECT ON SCHEMA <your_catalog>.quest TO `<service_principal_name>`;
```

### App returns "degraded" health

Check that:
- The SQL warehouse is running
- The app service principal has SELECT access to the Quest tables
- Visit `https://YOUR_APP_URL/api/health` to see the exact status

### Frontend build fails

Make sure you have Node.js 18+ (`node --version`). If `npm install` fails, delete `frontend/node_modules/` and try again:

```bash
rm -rf frontend/node_modules
./deploy.sh
```

### Deploy script fails mid-way

The script is safe to re-run. It's idempotent: it won't duplicate resources or data. Just fix the underlying issue and run `./deploy.sh` again.

---

## Manual Deployment

If you prefer not to use the deploy script, here are the individual steps:

```bash
# 1. Authenticate
databricks auth login --host https://YOUR_WORKSPACE.cloud.databricks.com

# 2. Edit databricks.yml — set your workspace URL under targets > dev > workspace > host

# 3. Build frontend
cd frontend && npm install && npm run build && cd ..

# 4. Deploy
databricks bundle deploy --target dev \
  --var warehouse_id=YOUR_WAREHOUSE_ID \
  --var quest_catalog=YOUR_CATALOG_NAME \
  --var quest_schema=quest \
  --var lakebase_host="" \
  --var lakebase_db=quest_db

# 5. Run scoring pipeline
databricks bundle run quest_scoring_pipeline --target dev \
  --var warehouse_id=YOUR_WAREHOUSE_ID \
  --var quest_catalog=YOUR_CATALOG_NAME \
  --var quest_schema=quest \
  --var lakebase_host="" \
  --var lakebase_db=quest_db

# 6. Get app URL
databricks apps get databricks-quest
```

---

## Estimated Cost

| Component | Usage | Est. Daily Cost |
|-----------|-------|-----------------|
| Databricks App | Always on, minimal compute | ~$1-2 |
| SQL Warehouse | Wakes for scoring + user queries (~1-2 hrs/day) | ~$1-3 |
| Scoring Job | 6 runs/day x 3-5 min each | ~$0.30-0.60 |
| Delta Storage | KB-MB of scored tables | ~$0.00 |
| **Total** | | **~$2-5/day** |

If your workspace already has a running SQL Warehouse, Quest's incremental cost is just the app compute and job runs: about $1-2/day.

---

## Glossary

| Term | What it is |
|------|------------|
| **Databricks App** | A web application hosted on your workspace. Gets its own URL, runs under a service principal, users log in with workspace credentials. |
| **Databricks Asset Bundle (DAB)** | Infrastructure-as-code for Databricks. The `databricks.yml` file defines what to deploy. |
| **System tables** | Read-only tables Databricks maintains about your workspace: who ran what, when, how much compute was used. They live in the `system` catalog. |
| **Lakebase** | Databricks' managed PostgreSQL. Optional but makes the app much faster. |
| **Service principal** | A machine identity for apps. When you deploy a Databricks App, it gets its own SP automatically. |
| **Unity Catalog** | Databricks' data governance layer. Organizes data into catalogs > schemas > tables. |
| **SQL Warehouse** | Compute for running SQL queries. The app uses one to read scored data. |
