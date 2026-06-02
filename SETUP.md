# Databricks Quest -- Deployment Guide

This guide walks you through deploying Databricks Quest from scratch. The deploy script handles everything automatically. You just need to install a few tools and run one command.

---

## Step 1: Install Prerequisites

You need four tools on your machine before you start. If any are missing, install them first.

### Databricks CLI (v0.285 or newer)

Check if you have it:

```bash
databricks --version
```

If it's missing or below v0.285, install it:

```bash
# macOS
brew install databricks/tap/databricks

# Linux / Windows
curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh
```

Full docs: [docs.databricks.com/dev-tools/cli/install](https://docs.databricks.com/en/dev-tools/cli/install.html)

### Node.js (v18 or newer)

Check if you have it:

```bash
node --version
```

If it's missing, install it from [nodejs.org](https://nodejs.org/) or:

```bash
brew install node
```

### psql (PostgreSQL client)

Check if you have it:

```bash
psql --version
```

If it's missing:

```bash
# macOS
brew install postgresql@16

# Linux (Debian/Ubuntu)
sudo apt install postgresql-client
```

### A Databricks workspace

You need a Databricks workspace with:
- **Unity Catalog** enabled (on by default for new workspaces)
- **System tables** enabled (ask your workspace admin if unsure)

To verify system tables are working, open the SQL editor in your workspace and run:

```sql
SELECT * FROM system.billing.usage LIMIT 1
```

If it returns a row, you're good. If not, ask your workspace admin to [enable system tables](https://docs.databricks.com/en/administration-guide/system-tables/index.html).

---

## Step 2: Clone the Repo

```bash
git clone https://github.com/deepbasu123/databricks-quest.git
cd databricks-quest
```

---

## Step 3: Run the Deploy Script

```bash
./deploy.sh
```

That's it. The script walks you through everything interactively. Here's what it does:

### What happens when you run `./deploy.sh`

1. **Checks prerequisites** -- Verifies that the Databricks CLI, Node.js, npm, and psql are installed and at the right versions. Tells you exactly what to install if anything is missing.

2. **Authenticates** -- Checks if you're logged in to a Databricks workspace. If not, it asks for your workspace URL and opens your browser to log in.

3. **Selects a SQL Warehouse** -- Lists all SQL Warehouses in your workspace and lets you pick one by number. This warehouse is used by the scoring pipeline to read system tables.

4. **Asks for a catalog name** -- You choose where Quest's Delta tables will live (e.g. `quest_data`). The scoring pipeline creates this catalog and schema automatically if they don't exist.

5. **Builds the frontend** -- Runs `npm install` and `npm run build` in the `frontend/` directory. This compiles the React app into static files.

6. **Deploys to Databricks** -- Uses Databricks Asset Bundles to deploy three things to your workspace:
   - A **Databricks App** (the web UI)
   - A **scoring notebook** (reads system tables and computes missions)
   - A **scheduled job** that runs the scoring notebook every 4 hours

7. **Provisions Lakebase** -- Creates a Lakebase (managed PostgreSQL) project, database, and tables. Sets up indexes and grants the app's service principal read access. This gives the app sub-second response times.

8. **Runs the scoring pipeline** -- Executes the scoring notebook for the first time. This reads your workspace's system tables from the past 30 days, scores all missions, computes user profiles, builds the leaderboard, and awards badges. Takes 5-15 minutes depending on workspace size.

9. **Syncs data to Lakebase** -- Reads the scored Delta tables and writes them to Lakebase so the app can serve data instantly.

10. **Prints the app URL** -- Shows you the URL to open in your browser. You log in with your Databricks credentials.

The whole process takes about 15 minutes. Most of that time is the scoring pipeline processing system table data.

---

## Step 4: Open the App

The deploy script prints the URL at the end. It looks like:

```
https://databricks-quest-1234567890.aws.databricksapps.com
```

Open it in your browser. You'll be asked to authorize the app (one-time), then you'll see your Quest dashboard.

You can also get the URL anytime with:

```bash
databricks apps get databricks-quest
```

---

## After Deployment

### Data stays fresh automatically

A scheduled job runs the scoring pipeline every 4 hours. It re-reads system tables, rescores all missions, and updates profiles. No action needed from you.

### What was deployed

| Component | Description |
|-----------|-------------|
| **Databricks App** | React + FastAPI web app with its own URL |
| **Scoring Notebook** | Spark notebook uploaded to your workspace |
| **Scheduled Job** | Runs the scoring notebook every 4 hours |
| **Delta Tables** | 6 tables in `<catalog>.<schema>`: mission_completions, user_profile_snapshot, leaderboard, badges, notifications, user_points_fact |
| **Lakebase Database** | PostgreSQL database (`quest_db`) with the same 6 tables, synced from Delta |

### Permissions

The scoring pipeline automatically grants the app's service principal:
- `USE_CATALOG` on your Quest catalog
- `USE_SCHEMA` and `SELECT` on the Quest schema
- `CAN_USE` on the SQL Warehouse
- `SELECT` on all Lakebase tables

If the auto-grant fails (some workspaces restrict this), see the Troubleshooting section.

---

## Non-Interactive Deploy

If you already know your warehouse ID and catalog name, you can skip all prompts:

```bash
./deploy.sh --warehouse-id a1b2c3d4e5f67890 --catalog quest_data
```

All available flags:

| Flag | Description | Default |
|------|-------------|---------|
| `--warehouse NAME` | Select warehouse by name | Interactive prompt |
| `--warehouse-id ID` | Use this warehouse ID directly | Interactive prompt |
| `--catalog NAME` | Unity Catalog name for Quest data | Interactive prompt |
| `--schema NAME` | Schema name for Quest tables | `quest` |
| `--app-name NAME` | Custom app name | `databricks-quest` |
| `--profile NAME` | Databricks CLI profile to use | Default profile |
| `--target TARGET` | Bundle target (`dev` or `prod`) | `dev` |
| `--lakebase-host HOST` | Use an existing Lakebase endpoint | Auto-provisioned |
| `--skip-build` | Skip frontend build (reuse existing) | Builds every time |
| `--skip-scoring` | Skip running the scoring pipeline | Runs every time |

---

## System Tables Used

The scoring pipeline reads these tables that Databricks maintains automatically:

| Table | What It Tracks | Missions It Powers |
|-------|---------------|-------------------|
| `system.billing.usage` | All compute usage per user | First Steps, DBU milestones, consumption points, streaks, product breadth |
| `system.lakeflow.jobs` | Job creation and configuration | Job Creator, Scheduler, Multi-Task Orchestrator |
| `system.lakeflow.job_tasks` | Task definitions within jobs | Multi-Task Orchestrator |
| `system.lakeflow.job_run_timeline` | Job execution history | Consistent Operator |
| `system.lakeflow.pipelines` | Pipeline creation | Pipeline Builder, Auto Loader Pioneer |
| `system.lakeflow.pipeline_update_timeline` | Pipeline execution results | Pipeline Runner |
| `system.query.history` | SQL query execution | Data Explorer, Power Analyst, AI Function Builder |
| `system.access.audit` | Workspace actions | Genie Creator, Dashboard Designer, Alert Creator, Model Deployer, MLflow Experimenter |

---

## Estimated Cost

| Component | Usage | Est. Daily Cost |
|-----------|-------|-----------------|
| Databricks App | Always on, minimal compute | ~$1-2 |
| Lakebase | Scales to zero when idle | ~$0.50-1 |
| SQL Warehouse | Wakes for scoring (~30 min/day) | ~$0.50-1 |
| Scoring Job | 6 runs/day x 5 min each | ~$0.30-0.60 |
| Delta Storage | KB-MB of scored tables | ~$0.00 |
| **Total** | | **~$2-5/day** |

If your workspace already has a running SQL Warehouse, Quest's incremental cost is mainly the app compute and Lakebase.

---

## Troubleshooting

### "Setup Required" message in the app

This means the app can't read data from Lakebase. Common causes:

1. **Scoring pipeline hasn't run yet.** The deploy script runs it automatically, but if it failed, re-run it from your workspace under Workflows > Job Runs.

2. **Lakebase data not synced.** The deploy script syncs data after the pipeline runs. If this step failed, you can re-run `./deploy.sh --skip-build` to retry.

3. **App environment variables not set.** The app needs `LAKEBASE_HOST` and `LAKEBASE_DB` configured. Re-running `./deploy.sh --skip-build --skip-scoring` will fix this.

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

### Frontend build fails

Make sure you have Node.js 18+ (`node --version`). If `npm install` fails, delete `frontend/node_modules/` and try again:

```bash
rm -rf frontend/node_modules
./deploy.sh
```

### Deploy script fails mid-way

The script is safe to re-run. It won't duplicate resources or data. Fix the underlying issue and run `./deploy.sh` again.

### CLI auth token expires during deployment

If you see errors about expired tokens or "forced token refresh", re-authenticate and retry:

```bash
databricks auth login --host https://YOUR_WORKSPACE.cloud.databricks.com
./deploy.sh --skip-build
```

---

## Glossary

| Term | What it is |
|------|------------|
| **Databricks App** | A web application hosted on your workspace. Gets its own URL, runs under a service principal, users log in with workspace credentials. |
| **Databricks Asset Bundle (DAB)** | Infrastructure-as-code for Databricks. The `databricks.yml` file defines what to deploy. |
| **System tables** | Read-only tables Databricks maintains about your workspace: who ran what, when, how much compute was used. They live in the `system` catalog. |
| **Lakebase** | Databricks' managed PostgreSQL. Provides sub-second query responses for the app. |
| **Service principal** | A machine identity for apps. When you deploy a Databricks App, it gets its own SP automatically. |
| **Unity Catalog** | Databricks' data governance layer. Organizes data into catalogs > schemas > tables. |
| **SQL Warehouse** | Compute for running SQL queries. Used by the scoring pipeline to read system tables. |
