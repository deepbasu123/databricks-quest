export interface UserProfile {
  user_id: string
  display_name: string
  total_points: number
  level: string
  level_progress: LevelProgress
  current_streak: number
  max_streak: number
  badge_count: number
  missions_completed: number
  first_activity_date?: string
  last_activity_date?: string
  distinct_products_used: number
  badges: Badge[]
  setup_required?: boolean
}

export interface LevelProgress {
  level: string
  current_points: number
  level_floor: number
  level_ceiling: number
  progress_pct: number
}

export interface Mission {
  id: string
  name: string
  description: string
  points: number
  category: string
  award_type: string
  icon: string
  status: 'available' | 'completed'
  completed_at?: string
}

export interface LeaderboardEntry {
  user_id: string
  display_name: string
  total_points: number
  weekly_points: number
  monthly_points: number
  level: string
  all_time_rank: number
  weekly_rank: number
  monthly_rank: number
}

export interface Badge {
  user_id: string
  badge_id: string
  badge_name: string
  badge_icon: string
  earned_at: string
}

export interface Notification {
  notification_type: string
  title: string
  message: string
  mission_id: string
  points: number
  created_at: string
}

export interface AdminStats {
  total_users: number
  total_mission_completions: number
  top_missions: { mission_id: string; mission_name: string; completions: number }[]
  level_distribution: { level: string; cnt: number }[]
  last_refresh: string | null
  setup_required?: boolean
}

export interface PipelineStatus {
  status: string
  last_run: string | null
  total_events_scored: number
}

export type Page = 'dashboard' | 'missions' | 'leaderboard' | 'admin' | 'federation'

// ── Multi-workspace federation (ADR_006) ────────────────────────────────────

export type QuestRole = 'standalone' | 'master' | 'child'

export interface FederationStatus {
  role: QuestRole
  workspace_id: string | null
  event_slug: string | null
  event_id: string | null
  submitted_by?: string
  mapped: boolean
  db_connected?: boolean
  team: { team_id: string; team_name?: string; display_name?: string } | null
}

export interface TeamLeaderboardEntry {
  event_id: string
  team_id: string
  display_name: string | null
  total_points: number
  rank: number | null
  last_scored_at?: string | null
}

export interface FederationLeaderboard {
  leaderboard: TeamLeaderboardEntry[]
  you: TeamLeaderboardEntry | null
  mapped: boolean
  event_id: string | null
  workspace_id?: string | null
}

export interface EventWorkspace {
  workspace_id: string
  event_slug: string | null
  workspace_host: string | null
  app_url: string | null
  app_version: string | null
  status: string
  registered_at: string | null
  last_seen_at: string | null
  scoring_events: number
  points: number
  validations: number
  validation_passes: number
}

export interface UnmappedIdentity {
  event_id: string
  workspace_id: string
  lab_user_email: string
  scoring_events: number
  unattributed_points: number
  last_seen_at: string | null
}
