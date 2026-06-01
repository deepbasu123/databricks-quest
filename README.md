# Databricks Quest

A gamification app that turns Databricks platform adoption into a game. Users earn points for building pipelines, running jobs, creating dashboards, querying data, and more. Weekly swag prizes keep things competitive.

Built entirely on Databricks: system tables for usage tracking, Delta Lake for scoring, Lakebase (managed PostgreSQL) for fast reads, and Databricks Apps for hosting.

## How It Works

1. A **scoring pipeline** runs every 4 hours, reading Databricks system tables to detect what each user has done on the platform
2. It scores 10 missions (creating jobs, building pipelines, using Genie, etc.) and writes results to Delta tables
3. Optionally syncs scored data to **Lakebase** (managed PostgreSQL) for sub-second app response times
4. A **React + FastAPI app** runs as a Databricks App, showing each user their personalized dashboard, missions, leaderboard, and badges

Users log in with their existing workspace credentials. No separate accounts needed.

## What Users See

- **Dashboard** — Current level, points, streak, badges, and next missions to complete
- **Missions** — 10 missions across Data Engineering, Analytics, and Engagement categories
- **Leaderboard** — Top 10 users with weekly swag prizes (1st: hoodie/tshirt, 2nd: coffee cup/water bottle/notebook, 3rd: stickers)
- **Admin** — Pipeline health, user stats, mission completion charts, level distribution

## Missions

| Mission | Points | What To Do |
|---------|--------|------------|
| First Steps | 25 | Use any Databricks compute for the first time |
| Job Creator | 100 | Create your first Lakeflow Job |
| Pipeline Builder | 150 | Create your first Lakeflow Spark Declarative Pipeline |
| Pipeline Runner | 200 | Run a pipeline successfully |
| Scheduler | 150 | Set up a scheduled or CRON-triggered job |
| Auto Loader Pioneer | 250 | Use Auto Loader in a pipeline |
| Genie Creator | 200 | Create an AI/BI Genie space |
| Dashboard Designer | 150 | Create a Databricks Dashboard |
| Consistent Operator | 300 | Run jobs/pipelines on 7 days within a 30-day window (repeatable monthly) |
| Data Explorer | 150 | Execute 50+ SQL queries in a single week (repeatable weekly) |

## Levels

| Level | Points Required |
|-------|----------------|
| Bronze | 0 |
| Silver | 300 |
| Gold | 800 |
| Platinum | 2,000 |
| Elite | 5,000 |

---

## Deployment

Full step-by-step guide with explanations: **[SETUP.md](SETUP.md)**

### Prerequisites

- A Databricks workspace with Unity Catalog and system tables enabled
- [Databricks CLI](https://docs.databricks.com/en/dev-tools/cli/install.html) v0.200+
- [Node.js](https://nodejs.org/) 18+
- A SQL Warehouse ID from your workspace

### Quick Deploy (6 commands)

```bash
# 1. Clone and enter the repo
git clone https://github.com/deepbasu123/databricks-quest.git
cd databricks-quest

# 2. Set your workspace URL in databricks.yml (the only file you need to edit)
#    Change the host under targets > dev > workspace

# 3. Authenticate with your workspace
databricks auth login --host https://YOUR_WORKSPACE.cloud.databricks.com

# 4. Build the frontend
cd frontend && npm install && npm run build && cd ..

# 5. Deploy everything (app + scoring job + notebook)
databricks bundle deploy --target dev \
  --var warehouse_id=YOUR_WAREHOUSE_ID \
  --var quest_catalog=YOUR_CATALOG_NAME \
  --var lakebase_host="" \
  --var lakebase_db=quest_db

# 6. Run the scoring pipeline (creates catalog/schema/tables, scores missions,
#    grants app permissions — everything is automatic)
databricks bundle run quest_scoring_pipeline --target dev
```

Then open the app:

```bash
databricks apps get databricks-quest
# Open the URL from the output in your browser
```

That's it. The catalog, schema, tables, and app permissions are all created automatically by the scoring pipeline. A scheduled job re-runs the pipeline every 4 hours to keep data fresh.

---

## Architecture

```
System Tables (read-only)          Quest App (Databricks App)
  system.billing.usage                 React Frontend
  system.lakeflow.jobs           <---  FastAPI Backend
  system.lakeflow.pipelines            reads from Delta/Lakebase
  system.query.history
  system.access.audit
        |
        v
  Scoring Pipeline (every 4h)
  runs as serverless job
        |
        v
  Delta Tables                   Lakebase (optional)
  <catalog>.quest.*         ---> quest_db (PostgreSQL)
  mission_completions            for sub-second reads
  user_profile_snapshot
  leaderboard
  badges, notifications
```

## Tech Stack

- **Frontend**: React 18, TypeScript, Tailwind CSS, Lucide icons, Vite
- **Backend**: FastAPI, Python 3.10+
- **Data**: Spark SQL, Delta Lake, system tables
- **Database**: Lakebase (managed PostgreSQL) or SQL Warehouse
- **Deployment**: Databricks Asset Bundles (DAB), Databricks Apps
- **Auth**: Workspace OAuth (SSO) — users log in with their Databricks credentials

## Project Structure

```
databricks-quest/
  databricks.yml          # Bundle config — app, job, variables
  app/
    main.py               # FastAPI backend (API endpoints)
    app.yaml              # Databricks App config
    requirements.txt      # Python dependencies
    static/               # Built React app (gitignored, generated by npm run build)
  frontend/
    src/
      App.tsx             # Main app with sidebar navigation
      components/
        Dashboard.tsx     # User dashboard with stats and badges
        Missions.tsx      # Mission grid with completion status
        Leaderboard.tsx   # Top 10 with podium and swag prizes
        AdminPanel.tsx    # Admin stats and pipeline health
      types.ts            # TypeScript interfaces
    package.json
    vite.config.ts
  notebooks/
    scoring_pipeline.py   # Spark notebook that scores all missions
  SETUP.md                # Detailed deployment guide
```

## License

MIT
