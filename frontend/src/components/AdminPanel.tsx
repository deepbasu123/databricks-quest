import { useMemo } from 'react'
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Database,
  Gauge,
  Target,
  TrendingUp,
  Users,
  type LucideIcon,
} from 'lucide-react'
import { Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { AdminStats, PipelineStatus } from '../types'
import { useApi } from '../lib/api'
import { QuestCard } from './quest/QuestCard'
import { EmptyState, ErrorState, Skeleton, SkeletonText } from './quest/States'

const LEVEL_COLORS: Record<string, string> = {
  Bronze: '#CD7F32',
  Silver: '#CBD5E1',
  Gold: '#F5B72E',
  Platinum: '#A78BFA',
  Elite: '#FF5F1F',
}

const BAR_COLORS = ['#FF5F1F', '#00C2D7', '#8B5CF6', '#22C55E', '#F43F5E', '#3B82F6', '#F5B72E', '#EC4899']

const TOOLTIP_STYLE = { background: '#0D1320', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 10, color: '#F1F5F9' }

export default function AdminPanel() {
  const stats = useApi<AdminStats>('/api/admin/stats')
  const pipeline = useApi<PipelineStatus>('/api/admin/pipeline-status')

  const s = stats.data
  const p = pipeline.data
  const isHealthy = p?.status === 'healthy'
  const lastRefresh = s?.last_refresh ? new Date(s.last_refresh).toLocaleString() : 'Never'

  const missionChartData = useMemo(
    () => (s?.top_missions ?? []).slice(0, 8).map((m) => ({ name: m.mission_name || m.mission_id, completions: m.completions })),
    [s],
  )
  const levelChartData = useMemo(
    () => (s?.level_distribution ?? []).map((l) => ({ name: l.level, value: l.cnt, color: LEVEL_COLORS[l.level] || '#64748B' })),
    [s],
  )

  const avgCompletions = s && s.total_users > 0 ? (s.total_mission_completions / s.total_users).toFixed(1) : '0'
  const adoptionBreadth = s ? s.level_distribution.reduce((acc, l) => acc + (l.level !== 'Bronze' ? l.cnt : 0), 0) : 0

  if (stats.error && !s) {
    return (
      <div className="mx-auto max-w-[1280px]">
        <ErrorState message={stats.error} onRetry={() => { stats.reload(); pipeline.reload() }} />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-[1280px] space-y-5">
      <QuestCard className={isHealthy ? 'border-[#22C55E]/30' : 'border-[#F5B72E]/30'}>
        <div className="relative z-10 flex flex-wrap items-center gap-4">
          {pipeline.loading && !p ? (
            <Skeleton className="h-6 w-6 rounded-full" />
          ) : isHealthy ? (
            <CheckCircle2 className="h-6 w-6 shrink-0 text-[#22C55E]" />
          ) : (
            <AlertTriangle className="h-6 w-6 shrink-0 text-[#F5B72E]" />
          )}
          <div className="flex-1">
            <h3 className="font-semibold text-white">
              Scoring Pipeline: {isHealthy ? 'Healthy' : p?.status === 'not_initialized' ? 'Not Initialized' : 'Unknown'}
            </h3>
            <p className="mt-0.5 text-sm text-slate-400">
              Last refresh: {lastRefresh}
              {p && p.total_events_scored > 0 && <span className="ml-3">Events scored: {p.total_events_scored.toLocaleString()}</span>}
            </p>
          </div>
          <Clock className="h-5 w-5 text-slate-500" />
        </div>
      </QuestCard>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi icon={Users} label="Total Users" value={s ? s.total_users.toLocaleString() : undefined} accent="#00C2D7" loading={stats.loading && !s} />
        <Kpi icon={Target} label="Mission Completions" value={s ? s.total_mission_completions.toLocaleString() : undefined} accent="#FF5F1F" loading={stats.loading && !s} />
        <Kpi icon={Activity} label="Avg / User" value={s ? avgCompletions : undefined} accent="#8B5CF6" loading={stats.loading && !s} />
        <Kpi icon={Gauge} label="Past Bronze" value={s ? adoptionBreadth.toLocaleString() : undefined} detail="users leveled up" accent="#22C55E" loading={stats.loading && !s} />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <QuestCard title="Top Mission Completions" eyebrow="Adoption">
          {stats.loading && !s ? (
            <SkeletonText lines={6} />
          ) : missionChartData.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={missionChartData} layout="vertical" margin={{ left: 0, right: 16 }}>
                <XAxis type="number" tick={{ fill: '#94A3B8', fontSize: 12 }} axisLine={false} tickLine={false} />
                <YAxis type="category" dataKey="name" tick={{ fill: '#94A3B8', fontSize: 11 }} width={130} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: 'rgba(255,255,255,0.04)' }} />
                <Bar dataKey="completions" radius={[0, 6, 6, 0]}>
                  {missionChartData.map((_, i) => (
                    <Cell key={i} fill={BAR_COLORS[i % BAR_COLORS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <EmptyState icon={Target} title="No completions yet" message="Mission completions will appear once users start earning points." />
          )}
        </QuestCard>

        <QuestCard title="Level Distribution" eyebrow="Cohorts">
          {stats.loading && !s ? (
            <SkeletonText lines={6} />
          ) : levelChartData.length > 0 ? (
            <div className="flex items-center gap-6">
              <ResponsiveContainer width="55%" height={240}>
                <PieChart>
                  <Pie data={levelChartData} cx="50%" cy="50%" innerRadius={56} outerRadius={92} dataKey="value" stroke="none" paddingAngle={2}>
                    {levelChartData.map((entry, i) => (
                      <Cell key={i} fill={entry.color} />
                    ))}
                  </Pie>
                  <Tooltip contentStyle={TOOLTIP_STYLE} />
                </PieChart>
              </ResponsiveContainer>
              <div className="flex-1 space-y-2.5">
                {levelChartData.map((l) => (
                  <div key={l.name} className="flex items-center gap-2 text-sm">
                    <span className="h-3 w-3 rounded-full" style={{ background: l.color }} />
                    <span className="text-slate-300">{l.name}</span>
                    <span className="ml-auto font-semibold text-white">{l.value}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <EmptyState icon={TrendingUp} title="No level data yet" message="Level cohorts populate after the first scoring run." />
          )}
        </QuestCard>
      </div>

      <QuestCard title="Data Sources" eyebrow="Telemetry">
        <div className="grid gap-3 sm:grid-cols-3">
          {[
            { label: 'System Tables', detail: 'Compute, jobs & query telemetry' },
            { label: 'Delta Scoring', detail: 'Point fact & snapshot tables' },
            { label: 'Lakebase Read Model', detail: 'Low-latency profile & leaderboard' },
          ].map((src) => (
            <div key={src.label} className="flex items-start gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-4">
              <Database className="mt-0.5 h-5 w-5 shrink-0 text-[#00C2D7]" />
              <div>
                <p className="text-sm font-semibold text-white">{src.label}</p>
                <p className="mt-0.5 text-xs text-slate-400">{src.detail}</p>
              </div>
            </div>
          ))}
        </div>
      </QuestCard>
    </div>
  )
}

function Kpi({
  icon: Icon,
  label,
  value,
  detail,
  accent,
  loading,
}: {
  icon: LucideIcon
  label: string
  value?: string
  detail?: string
  accent: string
  loading?: boolean
}) {
  return (
    <div className="quest-card p-5">
      <div className="relative z-10">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl" style={{ background: `${accent}22`, color: accent }}>
            <Icon className="h-5 w-5" />
          </div>
          <span className="text-sm text-slate-400">{label}</span>
        </div>
        {loading ? (
          <Skeleton className="mt-3 h-8 w-20" />
        ) : (
          <p className="mt-3 text-3xl font-bold text-white">{value ?? '—'}</p>
        )}
        {detail && <p className="mt-1 text-xs text-slate-500">{detail}</p>}
      </div>
    </div>
  )
}
