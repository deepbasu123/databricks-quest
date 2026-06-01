# Databricks Quest - Deployment Guide

Deploy Databricks Quest on your workspace in about 10 minutes. This guide walks you through every step. No prior Databricks experience needed.

---

## Prerequisites

You need five things before you start. If any of these are missing, get them sorted first.

| # | What | How to check / get it |
|---|------|-----------------------|
| 1 | **Databricks workspace** with Unity Catalog | Log in at your workspace URL. Unity Catalog is on by default for new workspaces. Don't have one? [Free trial](https://www.databricks.com/try-databricks). |
| 2 | **System tables enabled** | In your workspace, open the SQL editor and run `SELECT * FROM system.billing.usage LIMIT 1`. If it returns a row, you're good. If not, ask your workspace admin to [enable system tables](https://docs.databricks.com/en/administration-guide/system-tables/index.html). |
| 3 | **Databricks CLI v0.200+** | Run `databricks --version`. If it's missing or too old: macOS `brew install databricks/tap/databricks`, Linux/Windows `curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh \| sh`. [Docs](https://docs.databricks.com/en/dev-tools/cli/install.html). |
| 4 | **Node.js 18+** | Run `node --version`. If missing: [nodejs.org](https://nodejs.org/) or `brew install node`. |
| 5 | **A SQL Warehouse ID** | In your workspace sidebar, click **SQL Warehouses**. Use an existing warehouse or create a new Serverless one. Open its **Connection Details** tab and copy the **Warehouse ID** (a long hex string like `a1b2c3d4e5f67890`). |

You do **not** need to create a catalog or schema in advance. The scoring pipeline handles that automatically.

---

## Step 1 — Clone the repo

```bash
git clone https://github.com/deepbasu123/databricks-quest.git
cd databricks-quest
```

---

## Step 2 — Set your workspace URL

Open `databricks.yml` in any text editor. Find the `targets` section near the bottom and replace the placeholder with your workspace URL:

```yaml
targets:
  dev:
    mode: development
    default: true
    workspace:
      host: https://YOUR_WORKSPACE.cloud.databricks.com   # <-- change this
```

Your workspace URL is in your browser's address bar when you're logged in. Examples:
- AWS: `https://my-workspace.cloud.databricks.com`
- Azure: `https://adb-1234567890.12.azuredatabricks.net`
- GCP: `https://1234567890.gcp.databricks.com`

Save the file. That's the only file you need to edit.

---

## Step 3 — Authenticate

Run this command (use your workspace URL from Step 2):

```bash
databricks auth login --host https://YOUR_WORKSPACE.cloud.databricks.com
```

Your browser opens. Sign in with your Databricks credentials. When you see "Successfully logged in", go back to your terminal.

Verify it worked:

```bash
databricks current-user me
```

You should see your email and user ID.

---

## Step 4 — Build the frontend

```bash
cd frontend
npm install
npm run build
cd ..
```

This compiles the React app into `app/static/`. You should see output ending with "built in X seconds". If `npm install` fails, delete `frontend/node_modules/` and try again.

---

## Step 5 — Deploy

This single command deploys the app, the scoring notebook, and a scheduled job to your workspace:

```bash
databricks bundle deploy --target dev \
  --var warehouse_id=YOUR_WAREHOUSE_ID \
  --var quest_catalog=YOUR_CATALOG_NAME \
  --var lakebase_host="" \
  --var lakebase_db=quest_db
```

Replace these two values:
- **`YOUR_WAREHOUSE_ID`** — the SQL Warehouse ID you copied in Prerequisites (e.g. `a1b2c3d4e5f67890`)
- **`YOUR_CATALOG_NAME`** — any name you want (e.g. `quest_data`). The pipeline will create it if it doesn't exist.

Leave `lakebase_host` as an empty string for now. You can add Lakebase later for faster performance (see the optional section below).

When it finishes, you'll see output listing the deployed resources.

> **What just got deployed?**
> - A **Databricks App** (the web UI, accessible at its own URL)
> - A **notebook** uploaded to your workspace (the scoring pipeline)
> - A **scheduled job** that runs the scoring pipeline every 4 hours

---

## Step 6 — Run the scoring pipeline

The scoring pipeline reads your workspace's system tables, scores missions, and populates all the data the app needs. Run it once now:

```bash
databricks bundle run quest_scoring_pipeline --target dev
```

This takes 2-5 minutes. Watch the terminal for progress. When it finishes, it will print a summary showing how many users were scored and how many mission completions were found.

The pipeline does all of this automatically:
- Creates the catalog (if it doesn't exist)
- Creates a `quest` schema inside the catalog
- Creates 6 Delta tables
- Scores all 10 missions by reading system tables
- Builds user profiles, leaderboards, and badges
- Grants the app's service principal read access to the Quest tables
- The scheduled job will re-run all of this every 4 hours going forward

---

## Step 7 — Open the app

Get your app URL:

```bash
databricks apps get databricks-quest
```

Look for the `url` field in the output. It looks like:
```
https://databricks-quest-1234567890.cloud.databricksapps.com
```

Open that URL in your browser. You'll be redirected to log in with your Databricks credentials. After login, you'll see your Quest dashboard.

**That's it. You're done.**

---

## Verifying everything works

If you want to double-check that the app is healthy:

```bash
# Check app health (should return status: ok, db_connected: true)
curl -s https://YOUR_APP_URL/api/health | python3 -m json.tool

# Check the scoring job status in your workspace
databricks jobs list --output json | python3 -c "
import sys, json
for j in json.load(sys.stdin)['jobs']:
    if 'Quest' in j.get('settings',{}).get('name',''):
        print(f\"Job: {j['settings']['name']}\")
        print(f\"Job ID: {j['job_id']}\")
"
```

---

## Optional: Lakebase Integration

By default, the app queries a SQL Warehouse to load data. This works fine but responses take 2-5 seconds. **Lakebase** (Databricks' managed PostgreSQL) brings that down to sub-second.

This section is optional. Skip it if you just want to get Quest running.

### Requirements

- Databricks CLI **v0.285.0+** (run `databricks --version`, upgrade with `brew upgrade databricks/tap/databricks`)
- `psql` client installed (`brew install postgresql@16` on macOS)

### 1. Create a Lakebase project

```bash
databricks postgres create-project databricks-quest \
  --json '{"spec": {"display_name": "Databricks Quest"}}' \
  --no-wait
```

### 2. Wait for it to be ready (1-2 minutes)

```bash
databricks postgres list-endpoints projects/databricks-quest/branches/production
```

Repeat until `current_state` shows **ACTIVE**. Copy the `host` value from the output.

### 3. Create the database and tables

```bash
# Get connection details
HOST=$(databricks postgres list-endpoints projects/databricks-quest/branches/production \
  -o json | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['status']['hosts']['host'])")
TOKEN=$(databricks postgres generate-database-credential \
  projects/databricks-quest/branches/production/endpoints/primary -o json \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
EMAIL=$(databricks current-user me -o json | python3 -c "import sys,json; print(json.load(sys.stdin)['userName'])")

# Create database
PGPASSWORD=$TOKEN psql "host=$HOST port=5432 dbname=postgres user=$EMAIL sslmode=require" \
  -c "CREATE DATABASE quest_db;"

# Create tables
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
```

### 4. Grant the app's service principal access to Lakebase

```bash
# Get the app's service principal client ID
SP_CLIENT_ID=$(databricks apps get databricks-quest -o json \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['service_principal_client_id'])")

# Create an OAuth role for the SP
databricks postgres create-role projects/databricks-quest/branches/production \
  --role-id quest-sp \
  --json "{\"spec\": {\"identity_type\": \"SERVICE_PRINCIPAL\", \"postgres_role\": \"$SP_CLIENT_ID\", \"auth_method\": \"LAKEBASE_OAUTH_V1\", \"membership_roles\": [\"DATABRICKS_SUPERUSER\"]}}"

# Grant SELECT on all tables
PGPASSWORD=$TOKEN psql "host=$HOST port=5432 dbname=quest_db user=$EMAIL sslmode=require" -c "
GRANT SELECT ON ALL TABLES IN SCHEMA public TO \"$SP_CLIENT_ID\";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO \"$SP_CLIENT_ID\";
"
```

### 5. Redeploy with Lakebase enabled

```bash
databricks bundle deploy --target dev \
  --var warehouse_id=YOUR_WAREHOUSE_ID \
  --var quest_catalog=YOUR_CATALOG_NAME \
  --var lakebase_host=YOUR_LAKEBASE_HOST \
  --var lakebase_db=quest_db
```

Replace `YOUR_LAKEBASE_HOST` with the host you got in step 2 (e.g. `ep-abc123.database.us-east-1.cloud.databricks.com`).

### 6. Re-run the scoring pipeline to sync data

```bash
databricks bundle run quest_scoring_pipeline --target dev
```

The pipeline will now sync all scored data from Delta tables into Lakebase on every run. Your app should respond in under a second.

---

## What Gets Deployed

| Component | Description |
|-----------|-------------|
| **Databricks App** | React + FastAPI web app with gamification UI |
| **Scoring Notebook** | Reads system tables, computes missions/points/badges |
| **Scheduled Job** | Runs the scoring notebook every 4 hours |
| **Delta Tables** | `<catalog>.quest.*` — mission_completions, user_profile_snapshot, leaderboard, badges, notifications, user_points_fact |

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
databricks bundle run quest_scoring_pipeline --target dev
```

Then check the job run output in your workspace under **Workflows** > **Job Runs**.

### "Table not found" errors

The scoring pipeline creates all tables on its first run. Make sure it completed successfully before opening the app.

### No users on the leaderboard

System tables can take a few hours to populate after workspace creation. The scoring pipeline only finds users who have actual compute usage recorded in `system.billing.usage`. If your workspace is brand new, wait a few hours and run the pipeline again.

### Permission errors on system tables

Ask your workspace admin to:
1. Enable system tables (Admin Console > System Tables)
2. Grant you SELECT access to the relevant `system.*` schemas:

```sql
GRANT USE_CATALOG ON CATALOG system TO `your_email@company.com`;
GRANT USE_SCHEMA ON SCHEMA system.billing TO `your_email@company.com`;
GRANT SELECT ON SCHEMA system.billing TO `your_email@company.com`;
-- Repeat for: system.lakeflow, system.query, system.access
```

### Auto-grant failed for app permissions

If the pipeline prints a warning about auto-grant failing, the app won't be able to read Quest tables. Ask your workspace admin to run:

```sql
-- Find SP name with: databricks apps get databricks-quest
GRANT USE_CATALOG ON CATALOG <your_catalog> TO `<service_principal_name>`;
GRANT USE_SCHEMA ON SCHEMA <your_catalog>.quest TO `<service_principal_name>`;
GRANT SELECT ON SCHEMA <your_catalog>.quest TO `<service_principal_name>`;
```

### App returns "degraded" health

Check that:
- The SQL warehouse is running (if not using Lakebase)
- The Lakebase endpoint is ACTIVE (if using Lakebase)
- The app service principal has SELECT access to the Quest tables

### Frontend build fails

Make sure you have Node.js 18+ (`node --version`). If `npm install` fails, delete `frontend/node_modules/` and try again.

### CLI says "unknown command postgres"

Your Databricks CLI is older than v0.285.0. Upgrade:
- macOS: `brew upgrade databricks/tap/databricks`
- Other: Reinstall from [docs.databricks.com](https://docs.databricks.com/en/dev-tools/cli/install.html)

---

## Glossary

| Term | What it is |
|------|------------|
| **Databricks App** | A web application hosted on your workspace. Gets its own URL, runs under a service principal, users log in with workspace credentials. |
| **Databricks Asset Bundle (DAB)** | Infrastructure-as-code for Databricks. The `databricks.yml` file defines what to deploy. Think Terraform, but for Databricks resources. |
| **System tables** | Read-only tables Databricks maintains about your workspace: who ran what, when, how much compute was used. They live in the `system` catalog. |
| **Lakebase** | Databricks' managed PostgreSQL. Gives you a Postgres database inside your Databricks environment. Optional but makes the app much faster. |
| **Service principal** | A machine identity for apps and automation. When you deploy a Databricks App, it gets its own SP automatically. |
| **Unity Catalog** | Databricks' data governance layer. Organizes data into catalogs > schemas > tables. |
| **SQL Warehouse** | Compute for running SQL queries. The app uses one to read scored data (unless you set up Lakebase). |
