# Databricks Quest - Setup Guide

Step-by-step instructions to deploy Databricks Quest on your workspace. No prior Databricks experience needed.

## What You'll Need

Before starting, make sure you have:

1. **A Databricks workspace** with Unity Catalog enabled
   - If you don't have one, sign up at [databricks.com](https://www.databricks.com/try-databricks) for a free trial
   - Unity Catalog is enabled by default on new workspaces

2. **System tables enabled** on your workspace
   - System tables are read-only tables that Databricks provides with usage data about your workspace
   - They live in the `system` catalog (e.g. `system.billing.usage`, `system.lakeflow.jobs`)
   - If you don't see a `system` catalog in your workspace, ask your workspace admin to enable system tables
   - Docs: [Databricks system tables](https://docs.databricks.com/en/administration-guide/system-tables/index.html)

3. **Databricks CLI** installed on your computer
   - macOS: `brew install databricks/tap/databricks`
   - Linux/Windows: `curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh`
   - Verify: `databricks --version` (needs v0.200.0+)
   - Docs: [Install the Databricks CLI](https://docs.databricks.com/en/dev-tools/cli/install.html)

4. **Node.js 18+** for building the frontend
   - Download from [nodejs.org](https://nodejs.org/) or use `brew install node`
   - Verify: `node --version`

5. **A SQL Warehouse** running on your workspace
   - In your Databricks workspace, go to **SQL Warehouses** in the left sidebar
   - You can use an existing one or create a new Serverless warehouse
   - Copy the **Warehouse ID** (you'll need it later) - find it in the warehouse's **Connection Details** tab
   - Docs: [Create a SQL warehouse](https://docs.databricks.com/en/compute/sql-warehouse/create.html)

6. **A catalog name** for Quest data
   - Pick a name for the catalog that will hold Quest tables (e.g. `quest_data` or use an existing catalog)
   - The scoring pipeline will **auto-create** the catalog and schema if they don't exist
   - It will also **auto-grant** the app's service principal the required permissions
   - You don't need to create anything manually in advance

## Step 1: Clone the Repository

```bash
git clone https://github.com/deepbasu123/databricks-quest.git
cd databricks-quest
```

## Step 2: Configure Your Workspace

Open `databricks.yml` in a text editor. You need to update the `targets` section with your workspace URL:

```yaml
targets:
  dev:
    mode: development
    default: true
    workspace:
      host: https://YOUR_WORKSPACE.cloud.databricks.com
```

Replace `https://YOUR_WORKSPACE.cloud.databricks.com` with your actual workspace URL. You can find this in your browser's address bar when you're logged into Databricks (e.g. `https://adb-1234567890.12.azuredatabricks.net` for Azure, or `https://my-workspace.cloud.databricks.com` for AWS).

The variables (`warehouse_id`, `quest_catalog`, `lakebase_host`, `lakebase_db`) will be provided at deploy time - you don't need to edit them in the file.

## Step 3: Authenticate with Databricks

Run this command and replace the URL with your workspace URL:

```bash
databricks auth login --host https://YOUR_WORKSPACE.cloud.databricks.com
```

This opens your browser for login. Sign in with your Databricks credentials. Once done, you'll see a success message in your terminal.

To verify it worked:

```bash
databricks current-user me
```

This should show your email and user ID.

## Step 4: Build the Frontend

```bash
cd frontend
npm install
npm run build
cd ..
```

This installs React dependencies and compiles the frontend into `app/static/`. You should see a "built in X seconds" message.

## Step 5: Deploy to Your Workspace

Deploy the app, notebook, and scheduled job all at once:

```bash
databricks bundle deploy --target dev \
  --var warehouse_id=YOUR_WAREHOUSE_ID \
  --var quest_catalog=YOUR_CATALOG_NAME \
  --var lakebase_host="" \
  --var lakebase_db=quest_db
```

Replace:
- `YOUR_WAREHOUSE_ID` with your SQL Warehouse ID (from Step 5 of Prerequisites)
- `YOUR_CATALOG_NAME` with the catalog you want to use (from Step 6 of Prerequisites)

Set `lakebase_host` to empty string if you're not using Lakebase (the app will use the SQL warehouse instead).

This command deploys three things:
- **The Quest app** - a web application hosted on your Databricks workspace
- **The scoring pipeline notebook** - uploaded to your workspace files
- **A scheduled job** - runs the scoring pipeline every 4 hours

## Step 6: Run the Scoring Pipeline

The scoring pipeline reads your workspace's system tables and computes all the gamification data. Run it once to populate initial data:

```bash
databricks bundle run quest_scoring_pipeline --target dev
```

This takes 2-5 minutes. It will:
- Create a `quest` schema in your catalog
- Create 6 Delta tables (mission_completions, user_profile_snapshot, leaderboard, badges, notifications, user_points_fact)
- Score all 10 missions by querying system tables
- Build user profiles, leaderboards, and badges
- The scheduled job will re-run this automatically every 4 hours

## Step 7: Permissions (Automatic)

The scoring pipeline **automatically grants** the app's service principal read access to the Quest tables every time it runs. You don't need to do anything manually.

If auto-grant fails (e.g. you don't have admin permissions), you'll see a warning in the pipeline output. In that case, ask your workspace admin to run these SQL commands:

```sql
-- Replace <your_catalog> and <service_principal_name> with your actual values
-- Find the SP name with: databricks apps get databricks-quest
GRANT USE_CATALOG ON CATALOG <your_catalog> TO `<service_principal_name>`;
GRANT USE_SCHEMA ON SCHEMA <your_catalog>.quest TO `<service_principal_name>`;
GRANT SELECT ON SCHEMA <your_catalog>.quest TO `<service_principal_name>`;
```

## Step 8: Open the App

Find your app URL:

```bash
databricks apps get databricks-quest
```

Look for the `url` field (format: `https://databricks-quest-WORKSPACE_ID.cloud.databricksapps.com`).

Open it in your browser. You'll be redirected to log in with your Databricks workspace credentials. After login, you'll see your Quest dashboard with your current points, missions, and position on the leaderboard.

## Optional: Lakebase Integration

Lakebase is Databricks' managed PostgreSQL service. Using it makes the app load significantly faster (sub-second responses vs. 2-5 second SQL warehouse queries). This is optional but recommended for production use.

### Set Up Lakebase

1. **Create a Lakebase project** (requires Databricks CLI v0.285.0+):
   ```bash
   databricks postgres create-project databricks-quest \
     --json '{"spec": {"display_name": "Databricks Quest"}}' \
     --no-wait
   ```

2. **Wait for it to be ready** (1-2 minutes):
   ```bash
   databricks postgres list-endpoints projects/databricks-quest/branches/production
   ```
   Wait until `current_state` shows `ACTIVE`. Note the `host` value from the output.

3. **Create the database**:
   ```bash
   # Get connection details
   HOST=$(databricks postgres list-endpoints projects/databricks-quest/branches/production \
     -o json | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['status']['hosts']['host'])")
   TOKEN=$(databricks postgres generate-database-credential \
     projects/databricks-quest/branches/production/endpoints/primary -o json \
     | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
   EMAIL=$(databricks current-user me -o json | python3 -c "import sys,json; print(json.load(sys.stdin)['userName'])")

   # Create database and tables
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
   ```

4. **Create a Lakebase role for the app's service principal**:
   ```bash
   # Get the app's service principal client ID
   SP_CLIENT_ID=$(databricks apps get databricks-quest -o json \
     | python3 -c "import sys,json; print(json.load(sys.stdin)['service_principal_client_id'])")

   # Create an OAuth role so the SP can connect to Lakebase
   databricks postgres create-role projects/databricks-quest/branches/production \
     --role-id quest-sp \
     --json "{\"spec\": {\"identity_type\": \"SERVICE_PRINCIPAL\", \"postgres_role\": \"$SP_CLIENT_ID\", \"auth_method\": \"LAKEBASE_OAUTH_V1\", \"membership_roles\": [\"DATABRICKS_SUPERUSER\"]}}"

   # Grant the SP SELECT on all tables
   PGPASSWORD=$TOKEN psql "host=$HOST port=5432 dbname=quest_db user=$EMAIL sslmode=require" -c "
   GRANT SELECT ON ALL TABLES IN SCHEMA public TO \"$SP_CLIENT_ID\";
   ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO \"$SP_CLIENT_ID\";
   "
   ```

5. **Redeploy with Lakebase**:
   ```bash
   databricks bundle deploy --target dev \
     --var warehouse_id=YOUR_WAREHOUSE_ID \
     --var quest_catalog=YOUR_CATALOG_NAME \
     --var lakebase_host=YOUR_LAKEBASE_HOST \
     --var lakebase_db=quest_db
   ```

6. **Run the scoring pipeline again** to sync data to Lakebase:
   ```bash
   databricks bundle run quest_scoring_pipeline --target dev
   ```

## What Gets Deployed

| Component | Description |
|-----------|-------------|
| **Databricks App** | React + FastAPI web app with gamification UI |
| **Scoring Notebook** | Reads system tables, computes missions/points/badges |
| **Scheduled Job** | Runs the scoring notebook every 4 hours |
| **Delta Tables** | `<catalog>.quest.mission_completions`, `user_profile_snapshot`, `leaderboard`, `badges`, `notifications`, `user_points_fact` |

## System Tables Used

These are read-only tables that Databricks provides automatically. The scoring pipeline queries them to detect what each user has done.

| Table | What It Tracks | Missions It Powers |
|-------|---------------|-------------------|
| `system.billing.usage` | All compute usage per user | First Steps, activity streaks, product breadth |
| `system.lakeflow.jobs` | Job creation and configuration | Job Creator, Scheduler |
| `system.lakeflow.job_run_timeline` | Job execution history | Consistent Operator |
| `system.lakeflow.pipelines` | Pipeline creation | Pipeline Builder, Auto Loader Pioneer |
| `system.lakeflow.pipeline_update_timeline` | Pipeline execution results | Pipeline Runner |
| `system.query.history` | SQL query execution | Data Explorer |
| `system.access.audit` | Workspace actions (create dashboard, etc.) | Genie Creator, Dashboard Designer |

## Required Permissions

### For the scoring job (runs as you)

You need SELECT access to system tables. Most workspace users have this by default. If not:

```sql
GRANT USE_CATALOG ON CATALOG system TO `your_email@company.com`;
GRANT USE_SCHEMA ON SCHEMA system.billing TO `your_email@company.com`;
GRANT SELECT ON SCHEMA system.billing TO `your_email@company.com`;
-- Repeat for: system.lakeflow, system.query, system.access
```

### For the app service principal

The scoring pipeline **auto-grants** the app service principal read access to Quest tables on every run. No manual steps needed unless the auto-grant fails (see Step 7 for fallback).

### For regular app users

Nothing extra needed. Users log in with their workspace credentials and the app shows them their own data.

## Troubleshooting

### "Setup Required" message in the app
The scoring pipeline hasn't run yet, or it failed. Run it manually:
```bash
databricks bundle run quest_scoring_pipeline --target dev
```
Check the job run output in the Databricks workspace under **Workflows** > **Job Runs**.

### "Table not found" errors
The scoring pipeline creates all tables on its first run. Make sure it completed successfully before opening the app.

### No users on the leaderboard
System tables can take a few hours to populate after workspace creation. The scoring pipeline only detects users who have actual compute usage recorded in `system.billing.usage`.

### Permission errors on system tables
Ask your workspace admin to:
1. Enable system tables (Admin Console > System Tables)
2. Grant you SELECT access to the relevant `system.*` schemas

### App returns "degraded" health
Check that:
- The SQL warehouse is running (if not using Lakebase)
- The Lakebase endpoint is ACTIVE (if using Lakebase)
- The app service principal has SELECT access to the Quest tables or Lakebase database

### CLI says "unknown command postgres"
Your Databricks CLI is older than v0.285.0. Upgrade:
- macOS: `brew upgrade databricks/tap/databricks`
- Other: Reinstall from [docs.databricks.com](https://docs.databricks.com/en/dev-tools/cli/install.html)

### Frontend build fails
Make sure you have Node.js 18+ (`node --version`). If `npm install` fails, delete `frontend/node_modules/` and try again.

## Key Concepts for Databricks Newcomers

### What is a Databricks App?
A web application hosted directly on your Databricks workspace. It gets its own URL, runs under a service principal, and users log in with their workspace credentials. No separate hosting needed.

### What is a Databricks Asset Bundle (DAB)?
A way to define and deploy Databricks resources (apps, jobs, notebooks) as code using a `databricks.yml` config file. Think of it like Terraform but specifically for Databricks. The `databricks bundle deploy` command reads this file and creates everything on your workspace.

### What are system tables?
Read-only tables that Databricks maintains with metadata about your workspace: who ran what, when, how much compute was used, etc. They live in the `system` catalog and are the source of truth for Quest scoring.

### What is Lakebase?
Databricks' managed PostgreSQL service. It gives you a regular Postgres database that lives inside your Databricks environment. Quest can optionally use it for faster app reads (sub-second vs. 2-5 seconds with a SQL warehouse).

### What is a service principal?
A machine identity that Databricks creates for apps and automated processes. When you deploy a Databricks App, it automatically gets its own service principal. You grant this SP permissions just like you would a user.

### What is Unity Catalog?
Databricks' data governance layer. It organizes data into catalogs > schemas > tables. Quest stores its scored data in a catalog and schema you choose during setup.

### What is a SQL Warehouse?
A compute resource for running SQL queries. Quest's scoring pipeline uses serverless compute (no warehouse needed), but the app uses a SQL warehouse to query the scored data (unless you set up Lakebase).
