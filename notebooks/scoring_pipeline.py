# Databricks notebook source
# MAGIC %md
# MAGIC # Databricks Quest - Scoring Pipeline
# MAGIC
# MAGIC Reads system tables, detects mission completions, computes user profiles,
# MAGIC leaderboards, badges, and notifications. Idempotent via MERGE.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Configuration

# COMMAND ----------

dbutils.widgets.text("quest_catalog", "your_catalog", "Quest Catalog Name")
dbutils.widgets.text("quest_schema", "quest", "Quest Schema Name")
dbutils.widgets.text("app_name", "databricks-quest", "Databricks App Name (for auto-granting permissions)")

CATALOG = dbutils.widgets.get("quest_catalog")
SCHEMA = dbutils.widgets.get("quest_schema")
APP_NAME = dbutils.widgets.get("app_name")

def tbl(name):
    return f"`{CATALOG}`.`{SCHEMA}`.`{name}`"

print(f"Quest output: {CATALOG}.{SCHEMA}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1: Create Catalog, Schema, and Tables
# MAGIC Auto-creates the catalog and schema if they don't exist.

# COMMAND ----------

try:
    spark.sql(f"CREATE CATALOG IF NOT EXISTS `{CATALOG}`")
    print(f"Catalog `{CATALOG}` ready.")
except Exception as e:
    # Some workspaces require a MANAGED LOCATION for new catalogs.
    # If the catalog already exists, USE CATALOG will succeed anyway.
    print(f"Note: CREATE CATALOG failed ({e}). Checking if catalog already exists...")

try:
    spark.sql(f"USE CATALOG `{CATALOG}`")
except Exception as e:
    raise RuntimeError(
        f"Catalog `{CATALOG}` does not exist and could not be auto-created. "
        f"Please create it manually in your workspace (Catalog > + Add > Add a catalog) "
        f"and re-run the pipeline."
    ) from e

spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{CATALOG}`.`{SCHEMA}`")

# COMMAND ----------

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {tbl('mission_completions')} (
  user_id STRING,
  mission_id STRING,
  mission_name STRING,
  points_awarded INT,
  completed_at TIMESTAMP,
  period_start DATE,
  period_end DATE,
  scored_at TIMESTAMP
)
USING DELTA
""")

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {tbl('user_points_fact')} (
  user_id STRING,
  event_type STRING,
  mission_id STRING,
  points INT,
  reason STRING,
  event_timestamp TIMESTAMP,
  scored_at TIMESTAMP
)
USING DELTA
""")

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {tbl('user_profile_snapshot')} (
  user_id STRING,
  display_name STRING,
  total_points INT,
  level STRING,
  current_streak INT,
  max_streak INT,
  badge_count INT,
  missions_completed INT,
  first_activity_date DATE,
  last_activity_date DATE,
  distinct_products_used INT,
  updated_at TIMESTAMP
)
USING DELTA
""")

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {tbl('leaderboard')} (
  user_id STRING,
  display_name STRING,
  total_points INT,
  weekly_points INT,
  monthly_points INT,
  level STRING,
  all_time_rank INT,
  weekly_rank INT,
  monthly_rank INT,
  updated_at TIMESTAMP
)
USING DELTA
""")

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {tbl('badges')} (
  user_id STRING,
  badge_id STRING,
  badge_name STRING,
  badge_icon STRING,
  earned_at TIMESTAMP
)
USING DELTA
""")

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {tbl('notifications')} (
  user_id STRING,
  notification_type STRING,
  title STRING,
  message STRING,
  mission_id STRING,
  points INT,
  created_at TIMESTAMP
)
USING DELTA
""")

print("All tables created successfully.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2: Score Missions
# MAGIC Each mission is scored via a MERGE to ensure idempotency.

# COMMAND ----------

from datetime import datetime

NOW = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: First Steps (25 pts)
# MAGIC First billable Databricks usage per user.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    identity_metadata.run_as AS user_id,
    'first_steps' AS mission_id,
    'First Steps' AS mission_name,
    25 AS points_awarded,
    CAST(MIN(usage_date) AS TIMESTAMP) AS completed_at,
    MIN(usage_date) AS period_start,
    MIN(usage_date) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.billing.usage
  WHERE identity_metadata.run_as IS NOT NULL
    AND identity_metadata.run_as != ''
    AND identity_metadata.run_as NOT LIKE '%service-principal%'
    AND identity_metadata.run_as NOT LIKE '%ServicePrincipal%'
    AND usage_quantity > 0
  GROUP BY identity_metadata.run_as
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: First Steps")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Job Creator (100 pts)
# MAGIC Create first Lakeflow Job.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    creator_user_name AS user_id,
    'job_creator' AS mission_id,
    'Job Creator' AS mission_name,
    100 AS points_awarded,
    MIN(change_time) AS completed_at,
    CAST(MIN(change_time) AS DATE) AS period_start,
    CAST(MIN(change_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.lakeflow.jobs
  WHERE creator_user_name IS NOT NULL
    AND creator_user_name != ''
    AND delete_time IS NULL
  GROUP BY creator_user_name
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Job Creator")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Pipeline Builder (150 pts)
# MAGIC Create first Lakeflow Spark Declarative Pipeline.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    created_by AS user_id,
    'pipeline_builder' AS mission_id,
    'Pipeline Builder' AS mission_name,
    150 AS points_awarded,
    MIN(change_time) AS completed_at,
    CAST(MIN(change_time) AS DATE) AS period_start,
    CAST(MIN(change_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.lakeflow.pipelines
  WHERE created_by IS NOT NULL
    AND created_by != ''
    AND delete_time IS NULL
    AND pipeline_type = 'ETL_PIPELINE'
  GROUP BY created_by
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Pipeline Builder")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Pipeline Runner (200 pts)
# MAGIC First successful pipeline update.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    run_as_user_name AS user_id,
    'pipeline_runner' AS mission_id,
    'Pipeline Runner' AS mission_name,
    200 AS points_awarded,
    MIN(period_start_time) AS completed_at,
    CAST(MIN(period_start_time) AS DATE) AS period_start,
    CAST(MIN(period_start_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.lakeflow.pipeline_update_timeline
  WHERE result_state = 'COMPLETED'
    AND run_as_user_name IS NOT NULL
    AND run_as_user_name != ''
  GROUP BY run_as_user_name
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Pipeline Runner")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Scheduler (150 pts)
# MAGIC Create a scheduled or CRON-triggered job.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    j.creator_user_name AS user_id,
    'scheduler' AS mission_id,
    'Scheduler' AS mission_name,
    150 AS points_awarded,
    MIN(j.change_time) AS completed_at,
    CAST(MIN(j.change_time) AS DATE) AS period_start,
    CAST(MIN(j.change_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY workspace_id, job_id ORDER BY change_time DESC) AS rn
    FROM system.lakeflow.jobs
  ) j
  WHERE j.rn = 1
    AND j.trigger_type IN ('CRON', 'PERIODIC')
    AND j.delete_time IS NULL
    AND j.creator_user_name IS NOT NULL
    AND j.creator_user_name != ''
  GROUP BY j.creator_user_name
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Scheduler")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Auto Loader Pioneer (250 pts)
# MAGIC Use Auto Loader in a pipeline (inferred from pipeline type).

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    p.created_by AS user_id,
    'auto_loader_pioneer' AS mission_id,
    'Auto Loader Pioneer' AS mission_name,
    250 AS points_awarded,
    MIN(p.change_time) AS completed_at,
    CAST(MIN(p.change_time) AS DATE) AS period_start,
    CAST(MIN(p.change_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.lakeflow.pipelines p
  JOIN system.billing.usage u
    ON u.usage_metadata.dlt_pipeline_id = p.pipeline_id
  WHERE p.pipeline_type IN ('ETL_PIPELINE')
    AND p.delete_time IS NULL
    AND p.created_by IS NOT NULL
    AND p.created_by != ''
    AND u.billing_origin_product = 'DLT'
  GROUP BY p.created_by
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Auto Loader Pioneer")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Consistent Operator (300 pts, repeatable monthly)
# MAGIC Run pipelines or jobs on 7 distinct days within rolling 30 days.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  WITH daily_runs AS (
    SELECT
      j.creator_user_name AS user_id,
      CAST(r.period_start_time AS DATE) AS run_date
    FROM system.lakeflow.job_run_timeline r
    JOIN (
      SELECT job_id, creator_user_name, workspace_id,
             ROW_NUMBER() OVER (PARTITION BY workspace_id, job_id ORDER BY change_time DESC) AS rn
      FROM system.lakeflow.jobs
    ) j ON r.job_id = j.job_id AND r.workspace_id = j.workspace_id AND j.rn = 1
    WHERE r.result_state IS NOT NULL
      AND j.creator_user_name IS NOT NULL AND j.creator_user_name != ''
      AND r.period_start_time >= DATE_SUB(CURRENT_DATE(), 30)
    UNION
    SELECT
      run_as_user_name AS user_id,
      CAST(period_start_time AS DATE) AS run_date
    FROM system.lakeflow.pipeline_update_timeline
    WHERE result_state IS NOT NULL
      AND run_as_user_name IS NOT NULL AND run_as_user_name != ''
      AND period_start_time >= DATE_SUB(CURRENT_DATE(), 30)
  )
  SELECT
    user_id,
    'consistent_operator' AS mission_id,
    'Consistent Operator' AS mission_name,
    300 AS points_awarded,
    CAST(MAX(run_date) AS TIMESTAMP) AS completed_at,
    DATE_SUB(CURRENT_DATE(), 30) AS period_start,
    CURRENT_DATE() AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM daily_runs
  GROUP BY user_id
  HAVING COUNT(DISTINCT run_date) >= 7
) AS source
ON target.user_id = source.user_id
  AND target.mission_id = source.mission_id
  AND target.period_start = source.period_start
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Consistent Operator")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Data Explorer (150 pts, repeatable weekly)
# MAGIC Execute 50+ SQL queries in a week.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    executed_by AS user_id,
    'data_explorer' AS mission_id,
    'Data Explorer' AS mission_name,
    150 AS points_awarded,
    CAST(MAX(start_time) AS TIMESTAMP) AS completed_at,
    DATE_TRUNC('WEEK', start_time) AS period_start,
    DATE_ADD(DATE_TRUNC('WEEK', start_time), 6) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.query.history
  WHERE executed_by IS NOT NULL
    AND executed_by != ''
    AND statement_type IN ('SELECT', 'INSERT', 'MERGE', 'CREATE', 'ALTER')
    AND start_time >= DATE_SUB(CURRENT_DATE(), 30)
  GROUP BY executed_by, DATE_TRUNC('WEEK', start_time)
  HAVING COUNT(*) >= 50
) AS source
ON target.user_id = source.user_id
  AND target.mission_id = source.mission_id
  AND target.period_start = source.period_start
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Data Explorer")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Genie Creator (200 pts)
# MAGIC Create your first AI/BI Genie space.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    user_identity.email AS user_id,
    'genie_creator' AS mission_id,
    'Genie Creator' AS mission_name,
    200 AS points_awarded,
    MIN(event_time) AS completed_at,
    CAST(MIN(event_time) AS DATE) AS period_start,
    CAST(MIN(event_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.access.audit
  WHERE action_name = 'genieCreateSpace'
    AND response.status_code = 200
    AND user_identity.email IS NOT NULL
    AND user_identity.email != ''
  GROUP BY user_identity.email
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Genie Creator")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Mission: Dashboard Designer (150 pts)
# MAGIC Create your first Databricks Dashboard (Lakeview).

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('mission_completions')} AS target
USING (
  SELECT
    user_identity.email AS user_id,
    'dashboard_designer' AS mission_id,
    'Dashboard Designer' AS mission_name,
    150 AS points_awarded,
    MIN(event_time) AS completed_at,
    CAST(MIN(event_time) AS DATE) AS period_start,
    CAST(MIN(event_time) AS DATE) AS period_end,
    CAST('{NOW}' AS TIMESTAMP) AS scored_at
  FROM system.access.audit
  WHERE action_name = 'createDashboard'
    AND response.status_code = 200
    AND user_identity.email IS NOT NULL
    AND user_identity.email != ''
  GROUP BY user_identity.email
) AS source
ON target.user_id = source.user_id AND target.mission_id = source.mission_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Mission scored: Dashboard Designer")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3: Generate User Points Fact Table
# MAGIC Consolidate all mission completions into the points fact table.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('user_points_fact')} AS target
USING (
  SELECT
    user_id,
    'mission_completion' AS event_type,
    mission_id,
    points_awarded AS points,
    CONCAT('Completed mission: ', mission_name) AS reason,
    completed_at AS event_timestamp,
    scored_at
  FROM {tbl('mission_completions')}
) AS source
ON target.user_id = source.user_id
  AND target.mission_id = source.mission_id
  AND target.event_timestamp = source.event_timestamp
WHEN NOT MATCHED THEN INSERT *
""")

print("User points fact table updated.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4: Compute Activity Streaks

# COMMAND ----------

streaks_df = spark.sql(f"""
WITH daily_activity AS (
  SELECT
    identity_metadata.run_as AS user_id,
    CAST(usage_date AS DATE) AS activity_date
  FROM system.billing.usage
  WHERE identity_metadata.run_as IS NOT NULL
    AND identity_metadata.run_as != ''
    AND usage_quantity > 0
  GROUP BY identity_metadata.run_as, usage_date
),
streak_groups AS (
  SELECT
    user_id,
    activity_date,
    DATE_SUB(activity_date, CAST(ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY activity_date) AS INT)) AS streak_group
  FROM daily_activity
),
streak_lengths AS (
  SELECT
    user_id,
    streak_group,
    COUNT(*) AS streak_days,
    MAX(activity_date) AS streak_end
  FROM streak_groups
  GROUP BY user_id, streak_group
)
SELECT
  user_id,
  MAX(streak_days) AS max_streak,
  FIRST_VALUE(streak_days) OVER (PARTITION BY user_id ORDER BY streak_end DESC) AS current_streak
FROM streak_lengths
GROUP BY user_id, streak_days, streak_end
""")

streaks_df.createOrReplaceTempView("user_streaks")
print("Streak data computed.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 5: Compute Product Breadth

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TEMP VIEW user_products AS
SELECT
  identity_metadata.run_as AS user_id,
  COUNT(DISTINCT billing_origin_product) AS distinct_products
FROM system.billing.usage
WHERE identity_metadata.run_as IS NOT NULL
  AND identity_metadata.run_as != ''
  AND usage_quantity > 0
GROUP BY identity_metadata.run_as
""")

print("Product breadth computed.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 6: Build User Profile Snapshots

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE TEMP VIEW user_point_totals AS
SELECT
  user_id,
  SUM(points_awarded) AS total_points,
  COUNT(*) AS missions_completed
FROM {tbl('mission_completions')}
GROUP BY user_id
""")

spark.sql(f"""
CREATE OR REPLACE TEMP VIEW user_activity_dates AS
SELECT
  identity_metadata.run_as AS user_id,
  MIN(usage_date) AS first_activity_date,
  MAX(usage_date) AS last_activity_date
FROM system.billing.usage
WHERE identity_metadata.run_as IS NOT NULL
  AND identity_metadata.run_as != ''
GROUP BY identity_metadata.run_as
""")

spark.sql(f"""
CREATE OR REPLACE TEMP VIEW badge_counts AS
SELECT user_id, COUNT(*) AS badge_count
FROM {tbl('badges')}
GROUP BY user_id
""")

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('user_profile_snapshot')} AS target
USING (
  SELECT
    p.user_id,
    SPLIT(p.user_id, '@')[0] AS display_name,
    CAST(p.total_points AS INT) AS total_points,
    CASE
      WHEN p.total_points >= 5000 THEN 'Elite'
      WHEN p.total_points >= 2000 THEN 'Platinum'
      WHEN p.total_points >= 800 THEN 'Gold'
      WHEN p.total_points >= 300 THEN 'Silver'
      ELSE 'Bronze'
    END AS level,
    COALESCE(s.current_streak, 0) AS current_streak,
    COALESCE(s.max_streak, 0) AS max_streak,
    COALESCE(b.badge_count, 0) AS badge_count,
    CAST(p.missions_completed AS INT) AS missions_completed,
    a.first_activity_date,
    a.last_activity_date,
    COALESCE(pr.distinct_products, 0) AS distinct_products_used,
    CAST('{NOW}' AS TIMESTAMP) AS updated_at
  FROM user_point_totals p
  LEFT JOIN (
    SELECT user_id,
           MAX(max_streak) AS max_streak,
           MAX(current_streak) AS current_streak
    FROM user_streaks
    GROUP BY user_id
  ) s ON p.user_id = s.user_id
  LEFT JOIN user_activity_dates a ON p.user_id = a.user_id
  LEFT JOIN badge_counts b ON p.user_id = b.user_id
  LEFT JOIN user_products pr ON p.user_id = pr.user_id
) AS source
ON target.user_id = source.user_id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
""")

print("User profile snapshots updated.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 7: Build Leaderboard

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('leaderboard')} AS target
USING (
  WITH totals AS (
    SELECT
      user_id,
      SUM(points_awarded) AS total_points,
      SUM(CASE WHEN completed_at >= DATE_SUB(CURRENT_TIMESTAMP(), 7) THEN points_awarded ELSE 0 END) AS weekly_points,
      SUM(CASE WHEN completed_at >= DATE_TRUNC('MONTH', CURRENT_DATE()) THEN points_awarded ELSE 0 END) AS monthly_points
    FROM {tbl('mission_completions')}
    GROUP BY user_id
  )
  SELECT
    t.user_id,
    SPLIT(t.user_id, '@')[0] AS display_name,
    t.total_points,
    t.weekly_points,
    t.monthly_points,
    CASE
      WHEN t.total_points >= 5000 THEN 'Elite'
      WHEN t.total_points >= 2000 THEN 'Platinum'
      WHEN t.total_points >= 800 THEN 'Gold'
      WHEN t.total_points >= 300 THEN 'Silver'
      ELSE 'Bronze'
    END AS level,
    CAST(ROW_NUMBER() OVER (ORDER BY t.total_points DESC) AS INT) AS all_time_rank,
    CAST(ROW_NUMBER() OVER (ORDER BY t.weekly_points DESC) AS INT) AS weekly_rank,
    CAST(ROW_NUMBER() OVER (ORDER BY t.monthly_points DESC) AS INT) AS monthly_rank,
    CAST('{NOW}' AS TIMESTAMP) AS updated_at
  FROM totals t
) AS source
ON target.user_id = source.user_id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
""")

print("Leaderboard updated.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 8: Award Badges

# COMMAND ----------

# Badge: Platform Explorer - 4+ distinct billing_origin_product values
spark.sql(f"""
MERGE INTO {tbl('badges')} AS target
USING (
  SELECT
    user_id,
    'platform_explorer' AS badge_id,
    'Platform Explorer' AS badge_name,
    'compass' AS badge_icon,
    CAST('{NOW}' AS TIMESTAMP) AS earned_at
  FROM user_products
  WHERE distinct_products >= 4
) AS source
ON target.user_id = source.user_id AND target.badge_id = source.badge_id
WHEN NOT MATCHED THEN INSERT *
""")

# Badge: Consistent Contributor - 14-day streak
spark.sql(f"""
MERGE INTO {tbl('badges')} AS target
USING (
  SELECT
    user_id,
    'consistent_contributor' AS badge_id,
    'Consistent Contributor' AS badge_name,
    'flame' AS badge_icon,
    CAST('{NOW}' AS TIMESTAMP) AS earned_at
  FROM (
    SELECT user_id, MAX(max_streak) AS best_streak
    FROM user_streaks
    GROUP BY user_id
  )
  WHERE best_streak >= 14
) AS source
ON target.user_id = source.user_id AND target.badge_id = source.badge_id
WHEN NOT MATCHED THEN INSERT *
""")

# Badge: Pipeline Craftsman - completed 5+ pipeline-related missions
spark.sql(f"""
MERGE INTO {tbl('badges')} AS target
USING (
  SELECT
    user_id,
    'pipeline_craftsman' AS badge_id,
    'Pipeline Craftsman' AS badge_name,
    'wrench' AS badge_icon,
    CAST('{NOW}' AS TIMESTAMP) AS earned_at
  FROM {tbl('mission_completions')}
  WHERE mission_id IN ('pipeline_builder', 'pipeline_runner', 'auto_loader_pioneer', 'consistent_operator', 'scheduler', 'job_creator')
  GROUP BY user_id
  HAVING COUNT(DISTINCT mission_id) >= 5
) AS source
ON target.user_id = source.user_id AND target.badge_id = source.badge_id
WHEN NOT MATCHED THEN INSERT *
""")

print("Badges awarded.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 9: Generate Notifications for New Awards

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('notifications')} AS target
USING (
  SELECT
    user_id,
    'mission_complete' AS notification_type,
    CONCAT('Mission Complete: ', mission_name) AS title,
    CONCAT('You earned ', points_awarded, ' points for completing ', mission_name, '!') AS message,
    mission_id,
    points_awarded AS points,
    scored_at AS created_at
  FROM {tbl('mission_completions')}
  WHERE scored_at = CAST('{NOW}' AS TIMESTAMP)
) AS source
ON target.user_id = source.user_id
  AND target.mission_id = source.mission_id
  AND target.notification_type = source.notification_type
  AND target.created_at = source.created_at
WHEN NOT MATCHED THEN INSERT *
""")

# Badge notifications
spark.sql(f"""
MERGE INTO {tbl('notifications')} AS target
USING (
  SELECT
    user_id,
    'badge_earned' AS notification_type,
    CONCAT('Badge Unlocked: ', badge_name) AS title,
    CONCAT('Congratulations! You have earned the ', badge_name, ' badge!') AS message,
    badge_id AS mission_id,
    0 AS points,
    earned_at AS created_at
  FROM {tbl('badges')}
  WHERE earned_at = CAST('{NOW}' AS TIMESTAMP)
) AS source
ON target.user_id = source.user_id
  AND target.mission_id = source.mission_id
  AND target.notification_type = source.notification_type
WHEN NOT MATCHED THEN INSERT *
""")

print("Notifications generated.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 10: Update Badge Counts in Profiles

# COMMAND ----------

spark.sql(f"""
MERGE INTO {tbl('user_profile_snapshot')} AS target
USING (
  SELECT user_id, COUNT(*) AS badge_count
  FROM {tbl('badges')}
  GROUP BY user_id
) AS source
ON target.user_id = source.user_id
WHEN MATCHED AND target.badge_count != source.badge_count
  THEN UPDATE SET target.badge_count = source.badge_count, target.updated_at = CAST('{NOW}' AS TIMESTAMP)
""")

print("Badge counts synced to profiles.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 10b: Auto-Grant Permissions to App Service Principal
# MAGIC Looks up the Databricks App's service principal and grants it read access
# MAGIC to the Quest catalog and schema. This runs every time so new users don't
# MAGIC need to manually run SQL grants.

# COMMAND ----------

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import PermissionLevel, WarehouseAccessControlRequest, WarehousePermission

dbutils.widgets.text("warehouse_id", "", "SQL Warehouse ID")
WH_ID = dbutils.widgets.get("warehouse_id")

try:
    w = WorkspaceClient()
    app_info = w.apps.get(APP_NAME)

    # Try multiple attribute names — SDK versions vary
    sp_name = getattr(app_info, 'service_principal_name', None) \
        or getattr(app_info, 'effective_service_principal_name', None)
    sp_id = getattr(app_info, 'service_principal_id', None)
    sp_client_id = getattr(app_info, 'service_principal_client_id', None)

    # Fallback: look up SP display name by client ID
    if not sp_name and sp_client_id:
        try:
            for sp in w.service_principals.list(filter=f'applicationId eq "{sp_client_id}"'):
                sp_name = sp.display_name
                sp_id = sp.id
                break
        except Exception:
            pass

    if sp_name:
        print(f"App service principal: {sp_name} (ID: {sp_id})")

        # Grant catalog/schema permissions
        spark.sql(f"GRANT USE_CATALOG ON CATALOG `{CATALOG}` TO `{sp_name}`")
        spark.sql(f"GRANT USE_SCHEMA ON SCHEMA `{CATALOG}`.`{SCHEMA}` TO `{sp_name}`")
        spark.sql(f"GRANT SELECT ON SCHEMA `{CATALOG}`.`{SCHEMA}` TO `{sp_name}`")
        print(f"Granted catalog/schema access to {sp_name} on {CATALOG}.{SCHEMA}")

        # Grant CAN_USE on the SQL warehouse so the app can query data
        if WH_ID:
            try:
                w.warehouses.set_permissions(
                    warehouse_id=WH_ID,
                    access_control_list=[
                        WarehouseAccessControlRequest(
                            service_principal_name=sp_name,
                            permission_level=PermissionLevel.CAN_USE,
                        )
                    ],
                )
                print(f"Granted CAN_USE on warehouse {WH_ID} to {sp_name}")
            except Exception as wh_err:
                print(f"Warning: Warehouse grant failed (non-fatal): {wh_err}")
    else:
        print(f"Warning: Could not resolve service principal for app '{APP_NAME}'")
        print(f"  SP client ID: {sp_client_id}, SP ID: {sp_id}")
        print("  You may need to grant permissions manually. See SETUP.md troubleshooting.")
except Exception as e:
    print(f"Warning: Auto-grant failed (non-fatal): {e}")
    print("You may need to manually grant permissions. See SETUP.md troubleshooting.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Summary

# COMMAND ----------

total_users = spark.sql(f"SELECT COUNT(DISTINCT user_id) AS cnt FROM {tbl('user_profile_snapshot')}").first()["cnt"]
total_completions = spark.sql(f"SELECT COUNT(*) AS cnt FROM {tbl('mission_completions')}").first()["cnt"]
total_badges = spark.sql(f"SELECT COUNT(*) AS cnt FROM {tbl('badges')}").first()["cnt"]

print(f"""
Delta scoring complete.
  Users scored: {total_users}
  Mission completions: {total_completions}
  Badges awarded: {total_badges}
  Timestamp: {NOW}
""")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 11: Sync to Lakebase
# MAGIC Replicate scored Delta tables to Lakebase (PostgreSQL) for fast app reads.

# COMMAND ----------

dbutils.widgets.text("lakebase_host", "", "Lakebase Endpoint Host")
dbutils.widgets.text("lakebase_db", "quest_db", "Lakebase Database Name")

LB_HOST = dbutils.widgets.get("lakebase_host")
LB_DB = dbutils.widgets.get("lakebase_db")

if not LB_HOST:
    print("Lakebase host not configured — skipping sync.")
    dbutils.notebook.exit("OK_NO_LAKEBASE")

# COMMAND ----------

# MAGIC %pip install psycopg2-binary
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import psycopg2
import psycopg2.extras
from databricks.sdk import WorkspaceClient

# Re-read widgets after Python restart
LB_HOST = dbutils.widgets.get("lakebase_host")
LB_DB = dbutils.widgets.get("lakebase_db")
CATALOG = dbutils.widgets.get("quest_catalog")
SCHEMA = dbutils.widgets.get("quest_schema")

def tbl(name):
    return f"`{CATALOG}`.`{SCHEMA}`.`{name}`"

w = WorkspaceClient()
user_email = w.current_user.me().user_name

# Get a token for Lakebase authentication.
# On serverless, w.config.authenticate() may return a non-JWT token.
# Try the notebook context token first, then fall back to SDK methods.
def _get_lakebase_token():
    # Method 1: notebook context token (most reliable in Databricks notebooks)
    try:
        token = dbutils.notebook.entry_point.getDbutils().notebook().getContext().apiToken().get()
        if token:
            return token
    except Exception:
        pass
    # Method 2: SDK authenticate() header
    try:
        headers = w.config.authenticate()
        auth_header = headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:]
    except Exception:
        pass
    # Method 3: SDK token property
    return w.config.token or ""

token = _get_lakebase_token()
print(f"Token obtained (length: {len(token)})")

def get_pg_conn():
    return psycopg2.connect(
        host=LB_HOST, port=5432, dbname=LB_DB,
        user=user_email, password=token, sslmode="require"
    )

# Verify connectivity
conn = get_pg_conn()
conn.close()
print(f"Connected to Lakebase: {LB_HOST}/{LB_DB}")

# COMMAND ----------

TABLES_TO_SYNC = [
    ("mission_completions", [
        "user_id", "mission_id", "mission_name", "points_awarded",
        "completed_at", "period_start", "period_end", "scored_at"
    ]),
    ("user_points_fact", [
        "user_id", "event_type", "mission_id", "points",
        "reason", "event_timestamp", "scored_at"
    ]),
    ("user_profile_snapshot", [
        "user_id", "display_name", "total_points", "level",
        "current_streak", "max_streak", "badge_count", "missions_completed",
        "first_activity_date", "last_activity_date", "distinct_products_used", "updated_at"
    ]),
    ("leaderboard", [
        "user_id", "display_name", "total_points", "weekly_points",
        "monthly_points", "level", "all_time_rank", "weekly_rank",
        "monthly_rank", "updated_at"
    ]),
    ("badges", [
        "user_id", "badge_id", "badge_name", "badge_icon", "earned_at"
    ]),
    ("notifications", [
        "user_id", "notification_type", "title", "message",
        "mission_id", "points", "created_at"
    ]),
]

for table_name, columns in TABLES_TO_SYNC:
    print(f"Syncing {table_name}...")
    df = spark.sql(f"SELECT {', '.join(columns)} FROM {tbl(table_name)}")
    rows = df.collect()

    conn = get_pg_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"DELETE FROM {table_name}")
        if rows:
            placeholders = ", ".join(["%s"] * len(columns))
            insert_sql = f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({placeholders})"
            batch = []
            for row in rows:
                vals = tuple(row[c] for c in columns)
                batch.append(vals)
                if len(batch) >= 500:
                    psycopg2.extras.execute_batch(cur, insert_sql, batch)
                    batch = []
            if batch:
                psycopg2.extras.execute_batch(cur, insert_sql, batch)
        conn.commit()
        print(f"  {table_name}: {len(rows)} rows synced")
    except Exception as e:
        conn.rollback()
        print(f"  {table_name}: FAILED - {e}")
    finally:
        cur.close()
        conn.close()

print("Lakebase sync complete.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Summary

# COMMAND ----------

print(f"""
Scoring pipeline complete.
  Users scored: {total_users}
  Mission completions: {total_completions}
  Badges awarded: {total_badges}
  Lakebase sync: {LB_HOST}/{LB_DB}
  Timestamp: {NOW}
""")
