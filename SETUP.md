# Databricks Quest - Setup Guide

A gamification app that tracks and rewards Databricks platform usage through system tables.
Built as a Databricks Asset Bundle (DAB) for easy deployment.

## Prerequisites

1. **Databricks Workspace** with Unity Catalog enabled
2. **Databricks CLI** installed and authenticated (`pip install databricks-cli` or `brew install databricks`)
3. **Node.js 18+** (for building the frontend)
4. **System tables access** - your workspace must have the `system` catalog with billing, lakeflow, and query history tables enabled

## Quick Start (5 minutes)

### Step 1: Clone and configure

```bash
git clone https://github.com/deep-basu_data/databricks-quest.git
cd databricks-quest
```

Edit `databricks.yml` and update these variables for your workspace:

```yaml
variables:
  warehouse_id:
    default: YOUR_WAREHOUSE_ID    # Find this in SQL Warehouses page
  quest_catalog:
    default: YOUR_CATALOG_NAME    # An existing Unity Catalog catalog
  quest_schema:
    default: quest                # Schema will be created automatically
```

Update the target workspace host:

```yaml
targets:
  dev:
    workspace:
      host: https://YOUR_WORKSPACE.cloud.databricks.com
```

### Step 2: Authenticate with Databricks

```bash
databricks auth login --host https://YOUR_WORKSPACE.cloud.databricks.com
```

This opens a browser for OAuth login. Follow the prompts.

### Step 3: Build the frontend

```bash
cd frontend
npm install
npm run build
cd ..
```

This compiles the React app into `app/static/`.

### Step 4: Deploy everything

```bash
databricks bundle deploy --target dev
```

This deploys:
- The **Quest app** (React + FastAPI)
- The **scoring pipeline** notebook
- The **scheduled job** (runs every 4 hours)

### Step 5: Run the scoring pipeline

The scoring pipeline needs to run once to create tables and populate initial data:

```bash
databricks bundle run quest_scoring_pipeline --target dev
```

This reads your workspace's system tables and computes all missions, points, badges,
and leaderboards. It takes 2-5 minutes depending on data volume.

### Step 6: Open the app

Find your app URL:
```bash
databricks apps get databricks-quest
```

Navigate to the URL (format: `https://databricks-quest-WORKSPACE_ID.aws.databricksapps.com`).
Log in with your workspace credentials.

## What Gets Deployed

| Component | Description |
|-----------|-------------|
| **Databricks App** | React + FastAPI web app with gamification UI |
| **Scoring Notebook** | Reads system tables, computes missions/points/badges |
| **Scheduled Job** | Runs the scoring notebook every 4 hours |
| **Delta Tables** | `<catalog>.<schema>.mission_completions`, `user_profile_snapshot`, `leaderboard`, `badges`, `notifications`, `user_points_fact` |

## Required Permissions

### For the app service principal (auto-created):
```sql
-- Grant read access to quest tables
GRANT USE_CATALOG ON CATALOG <your_catalog> TO `<app_service_principal>`;
GRANT USE_SCHEMA ON SCHEMA <your_catalog>.quest TO `<app_service_principal>`;
GRANT SELECT ON SCHEMA <your_catalog>.quest TO `<app_service_principal>`;

-- Grant warehouse access
-- Done automatically via app.yaml resources config
```

### For the scoring job (runs as you):
```sql
-- System table access (usually granted by default to workspace users)
GRANT USE_CATALOG ON CATALOG system TO `<your_email>`;
GRANT USE_SCHEMA ON SCHEMA system.billing TO `<your_email>`;
GRANT SELECT ON SCHEMA system.billing TO `<your_email>`;
-- Repeat for system.lakeflow, system.query, system.access
```

### For regular app users:
No extra grants needed - the app uses the service principal to query data and personalizes
the view based on the authenticated user's email.

## System Tables Used

| Table | Purpose |
|-------|---------|
| `system.billing.usage` | First Steps mission, consumption tracking, activity streaks |
| `system.lakeflow.jobs` | Job Creator and Scheduler missions |
| `system.lakeflow.job_run_timeline` | Consistent Operator mission |
| `system.lakeflow.pipelines` | Pipeline Builder and Auto Loader missions |
| `system.lakeflow.pipeline_update_timeline` | Pipeline Runner mission |
| `system.query.history` | Data Explorer mission |

## Missions (v1)

| Mission | Points | Type |
|---------|--------|------|
| First Steps - first billable usage | 25 | One-time |
| Job Creator - create first job | 100 | One-time |
| Pipeline Builder - create first pipeline | 150 | One-time |
| Pipeline Runner - first successful pipeline run | 200 | One-time |
| Scheduler - create a CRON/scheduled job | 150 | One-time |
| Auto Loader Pioneer - use Auto Loader in a pipeline | 250 | One-time |
| Consistent Operator - 7 active days in 30 | 300 | Monthly |
| Data Explorer - 50+ SQL queries in a week | 150 | Weekly |

## Levels

| Level | Points |
|-------|--------|
| Bronze | 0 - 299 |
| Silver | 300 - 799 |
| Gold | 800 - 1,999 |
| Platinum | 2,000 - 4,999 |
| Elite | 5,000+ |

## Troubleshooting

**App shows "Setup Required"**
The scoring pipeline hasn't run yet. Run it manually:
```bash
databricks bundle run quest_scoring_pipeline --target dev
```

**"Table not found" errors**
The scoring pipeline creates all tables on first run. Make sure it completed successfully.

**No users showing on leaderboard**
System tables may take up to a few hours to populate. The scoring pipeline only finds users
with actual billable usage in the `system.billing.usage` table.

**Permission errors on system tables**
Ask your workspace admin to enable system tables and grant SELECT access to the relevant schemas.

## Architecture

```
[System Tables] --> [Scoring Pipeline (every 4h)] --> [Quest Delta Tables]
                                                           |
                                                    [FastAPI Backend]
                                                           |
                                                    [React Frontend]
                                                           |
                                                    [Workspace Users]
```

The app runs as a Databricks App with workspace OAuth authentication.
Users log in with their existing workspace credentials.
The scoring pipeline reads system tables (read-only) and writes to quest Delta tables.
The app reads from quest tables and displays personalized dashboards.
