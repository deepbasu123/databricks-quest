# Databricks Quest

A gamification app that turns Databricks platform adoption into a game. Users earn points for building pipelines, running jobs, creating dashboards, querying data, and more. Weekly swag prizes keep things competitive.

Built entirely on Databricks: system tables for usage tracking, Delta Lake for scoring, Lakebase for fast reads, and Databricks Apps for hosting. Users log in with their existing workspace credentials.

## How It Works

1. A **scoring pipeline** runs every 4 hours, reading Databricks system tables to detect what each user has done on the platform
2. It scores 30+ missions across Data Engineering, Analytics, AI/ML, and Engagement categories, plus continuous consumption points based on DBU spend
3. Scored data is synced to **Lakebase** (managed PostgreSQL) for sub-second reads
4. A **React + FastAPI app** runs as a Databricks App, showing each user their dashboard, missions, leaderboard, and badges

No separate accounts needed. Users log in with their workspace credentials.

---

## Deploy

Full step-by-step instructions: **[SETUP.md](SETUP.md)**

Short version:

```bash
git clone https://github.com/deepbasu123/databricks-quest.git
cd databricks-quest
./deploy.sh
```

The script handles everything: prerequisites check, authentication, warehouse selection, frontend build, Lakebase provisioning, app deployment, scoring pipeline, and data sync. Takes about 15 minutes end to end.

---

## What Users See

- **Dashboard** -- Current level, points, streak, badges, and next missions to complete
- **Missions** -- 30+ missions across Data Engineering, Analytics, AI/ML, Streaming, Consumption, and Engagement
- **Leaderboard** -- Top 10 users ranked by points, resets every Saturday. Weekly swag prizes for the top 3.
- **Admin** -- Pipeline health, user stats, mission completion charts, level distribution

## Missions

### Getting Started & Data Engineering
| Mission | Points | What To Do |
|---------|--------|------------|
| First Steps | 25 | Use any Databricks compute for the first time |
| Job Creator | 100 | Create your first Lakeflow Job |
| Pipeline Builder | 150 | Create your first Lakeflow Spark Declarative Pipeline |
| Pipeline Runner | 200 | Run a pipeline successfully |
| Scheduler | 150 | Set up a scheduled or CRON-triggered job |
| Auto Loader Pioneer | 250 | Use Auto Loader in a pipeline |
| Multi-Task Orchestrator | 200 | Create a workflow with 3+ tasks |
| Liquid Clustering Adopter | 200 | Enable Liquid Clustering on a table |

### Analytics
| Mission | Points | What To Do |
|---------|--------|------------|
| Genie Creator | 200 | Create an AI/BI Genie space |
| Dashboard Designer | 150 | Create a Databricks Dashboard |
| Data Explorer | 150 | Execute 50+ SQL queries in a single week (repeatable) |
| Power Analyst | 200 | Execute 200+ SQL queries in a single week (repeatable) |
| Alert Creator | 150 | Create a SQL Alert with a schedule |
| Dashboard Publisher | 200 | Publish a dashboard shared with 3+ viewers |

### AI / ML
| Mission | Points | What To Do |
|---------|--------|------------|
| Model Deployer | 300 | Deploy a model to a serving endpoint |
| AI Function Builder | 250 | Use ai_query() in a SQL statement |
| Vector Search Pioneer | 200 | Create a Vector Search index |
| MLflow Experimenter | 150 | Log 10+ MLflow experiment runs |

### Streaming
| Mission | Points | What To Do |
|---------|--------|------------|
| Stream Starter | 250 | Run a Structured Streaming job for 24+ hours |

### Consumption (DBU-based)
| Mission | Points | What To Do |
|---------|--------|------------|
| First 100 DBUs | 50 | Reach 100 lifetime DBUs |
| 1K DBU Club | 200 | Reach 1,000 lifetime DBUs |
| 10K DBU Club | 500 | Reach 10,000 lifetime DBUs |
| 100K DBU Club | 1,000 | Reach 100,000 lifetime DBUs |
| SQL Analyst | 100 | 50+ SQL Warehouse DBUs in a month (repeatable) |
| Job Runner | 100 | 50+ Jobs Compute DBUs in a month (repeatable) |
| ML Practitioner | 150 | Any Model Serving DBUs in a month (repeatable) |
| Pipeline Operator | 100 | 50+ DLT DBUs in a month (repeatable) |

Plus **continuous consumption points**: 1 point per 10 DBUs consumed, scored weekly. This keeps the leaderboard dynamic and rewards sustained platform usage.

### Engagement
| Mission | Points | What To Do |
|---------|--------|------------|
| Consistent Operator | 300 | Run jobs/pipelines on 7 days within 30 days (repeatable) |
| Daily Driver | 400 | Active on 20+ days in a 30-day window (repeatable) |
| Cross-Product Champion | 500 | Use 6+ distinct Databricks products in a month (repeatable) |

## Levels

| Level | Points Required |
|-------|----------------|
| Bronze | 0 |
| Silver | 300 |
| Gold | 800 |
| Platinum | 2,000 |
| Elite | 5,000 |

## Architecture

```
System Tables (read-only)          Quest App (Databricks App)
  system.billing.usage                 React Frontend
  system.lakeflow.jobs           <---  FastAPI Backend
  system.lakeflow.pipelines            reads from Lakebase
  system.query.history                 (sub-second queries)
  system.access.audit
        |
        v
  Scoring Pipeline (every 4h)
  runs as serverless job
        |
        v
  Delta Tables              --->  Lakebase (PostgreSQL)
  <catalog>.quest.*                quest_db
  mission_completions              synced after every
  user_profile_snapshot            pipeline run
  leaderboard
  badges, notifications
```

## Tech Stack

- **Frontend**: React 18, TypeScript, Tailwind CSS, Lucide icons, Vite
- **Backend**: FastAPI, Python 3.10+
- **Data**: Spark SQL, Delta Lake, system tables
- **Database**: Lakebase (managed PostgreSQL) for sub-second reads
- **Deployment**: Databricks Asset Bundles, Databricks Apps
- **Auth**: Workspace OAuth (SSO)

## Project Structure

```
databricks-quest/
  deploy.sh               # One-shot deployment script
  databricks.yml           # Bundle config (app, job, variables)
  app/
    main.py                # FastAPI backend (API endpoints)
    app.yaml               # Databricks App config
    requirements.txt       # Python dependencies
    static/                # Built React app (generated by npm run build)
  frontend/
    src/
      App.tsx              # Main app with sidebar navigation
      components/
        Dashboard.tsx      # User dashboard with stats and badges
        Missions.tsx       # Mission grid with completion status
        Leaderboard.tsx    # Top 10 with podium and swag prizes
        AdminPanel.tsx     # Admin stats and pipeline health
      types.ts             # TypeScript interfaces
    package.json
    vite.config.ts
  notebooks/
    scoring_pipeline.py    # Spark notebook that scores all missions
  SETUP.md                 # Full deployment guide & troubleshooting
```

## License

MIT
