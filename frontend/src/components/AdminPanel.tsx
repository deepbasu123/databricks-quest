import { useEffect, useState } from 'react'
import { Activity, Users, Target, Clock, CheckCircle2, AlertTriangle } from 'lucide-react'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, PieChart, Pie } from 'recharts'
import type { AdminStats, PipelineStatus } from '../types'

const LEVEL_COLORS: Record<string, string> = {
  Bronze: '#b45309',
  Silver: '#9ca3af',
  Gold: '#eab308',
  Platinum: '#a78bfa',
  Elite: '#f97316',
}

const BAR_COLORS = ['#f59e0b', '#06b6d4', '#8b5cf6', '#22c55e', '#f43f5e', '#6366f1', '#14b8a6', '#ec4899']

export default function AdminPanel() {
  const [stats, setStats] = useState<AdminStats | null>(null)
  const [pipeline, setPipeline] = useState<PipelineStatus | null>(null)

  useEffect(() => {
    fetch('/api/admin/stats').then(r => r.json()).then(setStats).catch(() => {})
    fetch('/api/admin/pipeline-status').then(r => r.json()).then(setPipeline).catch(() => {})
  }, [])

  if (!stats) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-8 h-8 border-4 border-amber-500 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  const isHealthy = pipeline?.status === 'healthy'
  const lastRefresh = stats.last_refresh
    ? new Date(stats.last_refresh).toLocaleString()
    : 'Never'

  const missionChartData = (stats.top_missions || []).map(m => ({
    name: m.mission_name || m.mission_id,
    completions: m.completions,
  }))

  const levelChartData = (stats.level_distribution || []).map(l => ({
    name: l.level,
    value: l.cnt,
    color: LEVEL_COLORS[l.level] || '#64748b',
  }))

  return (
    <div className="max-w-5xl space-y-6">
      {/* Pipeline status */}
      <div className={`card p-5 flex items-center gap-4 ${isHealthy ? 'border-green-500/30' : 'border-amber-500/30'}`}>
        {isHealthy ? (
          <CheckCircle2 className="w-6 h-6 text-green-400 shrink-0" />
        ) : (
          <AlertTriangle className="w-6 h-6 text-amber-400 shrink-0" />
        )}
        <div className="flex-1">
          <h3 className="font-semibold text-white">
            Scoring Pipeline: {isHealthy ? 'Healthy' : pipeline?.status === 'not_initialized' ? 'Not Initialized' : 'Unknown'}
          </h3>
          <p className="text-sm text-slate-400 mt-0.5">
            Last refresh: {lastRefresh}
            {pipeline && pipeline.total_events_scored > 0 && (
              <span className="ml-3">Total events scored: {pipeline.total_events_scored.toLocaleString()}</span>
            )}
          </p>
        </div>
        <Clock className="w-5 h-5 text-slate-500" />
      </div>

      {/* Key metrics */}
      <div className="grid grid-cols-3 gap-4">
        <div className="card p-5">
          <div className="flex items-center gap-3">
            <Users className="w-5 h-5 text-cyan-400" />
            <span className="text-sm text-slate-400">Total Users</span>
          </div>
          <p className="text-3xl font-bold text-white mt-2">{stats.total_users}</p>
        </div>
        <div className="card p-5">
          <div className="flex items-center gap-3">
            <Target className="w-5 h-5 text-amber-400" />
            <span className="text-sm text-slate-400">Mission Completions</span>
          </div>
          <p className="text-3xl font-bold text-white mt-2">{stats.total_mission_completions}</p>
        </div>
        <div className="card p-5">
          <div className="flex items-center gap-3">
            <Activity className="w-5 h-5 text-violet-400" />
            <span className="text-sm text-slate-400">Avg Completions/User</span>
          </div>
          <p className="text-3xl font-bold text-white mt-2">
            {stats.total_users > 0
              ? (stats.total_mission_completions / stats.total_users).toFixed(1)
              : '0'}
          </p>
        </div>
      </div>

      <div className="grid lg:grid-cols-2 gap-6">
        {/* Mission completions chart */}
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-4">Mission Completions</h3>
          {missionChartData.length > 0 ? (
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={missionChartData} layout="vertical" margin={{ left: 0, right: 20 }}>
                <XAxis type="number" tick={{ fill: '#94a3b8', fontSize: 12 }} axisLine={false} tickLine={false} />
                <YAxis type="category" dataKey="name" tick={{ fill: '#94a3b8', fontSize: 11 }} width={120} axisLine={false} tickLine={false} />
                <Tooltip
                  contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, color: '#f1f5f9' }}
                  cursor={{ fill: 'rgba(255,255,255,0.05)' }}
                />
                <Bar dataKey="completions" radius={[0, 4, 4, 0]}>
                  {missionChartData.map((_, i) => (
                    <Cell key={i} fill={BAR_COLORS[i % BAR_COLORS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-48 flex items-center justify-center text-slate-500 text-sm">No data yet</div>
          )}
        </div>

        {/* Level distribution */}
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-4">Level Distribution</h3>
          {levelChartData.length > 0 ? (
            <div className="flex items-center gap-6">
              <ResponsiveContainer width="50%" height={200}>
                <PieChart>
                  <Pie
                    data={levelChartData}
                    cx="50%"
                    cy="50%"
                    innerRadius={50}
                    outerRadius={80}
                    dataKey="value"
                    stroke="none"
                  >
                    {levelChartData.map((entry, i) => (
                      <Cell key={i} fill={entry.color} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, color: '#f1f5f9' }}
                  />
                </PieChart>
              </ResponsiveContainer>
              <div className="space-y-2">
                {levelChartData.map(l => (
                  <div key={l.name} className="flex items-center gap-2 text-sm">
                    <div className="w-3 h-3 rounded-full" style={{ background: l.color }} />
                    <span className="text-slate-300">{l.name}</span>
                    <span className="text-slate-500 ml-auto">{l.value}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="h-48 flex items-center justify-center text-slate-500 text-sm">No data yet</div>
          )}
        </div>
      </div>
    </div>
  )
}
