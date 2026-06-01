import { useEffect, useState } from 'react'
import {
  Zap, Target, TrendingUp, Calendar, Search,
  CheckCircle2, Circle, Repeat, Sparkles, LayoutDashboard,
} from 'lucide-react'
import type { Mission } from '../types'

const ICON_MAP: Record<string, typeof Zap> = {
  rocket: Zap,
  briefcase: Target,
  'git-branch': TrendingUp,
  'play-circle': Zap,
  clock: Calendar,
  'upload-cloud': TrendingUp,
  'calendar-check': Calendar,
  search: Search,
  sparkles: Sparkles,
  'layout-dashboard': LayoutDashboard,
}

const CATEGORY_COLORS: Record<string, string> = {
  'Getting Started': 'bg-green-500/20 text-green-400',
  'Data Engineering': 'bg-cyan-500/20 text-cyan-400',
  'Engagement': 'bg-violet-500/20 text-violet-400',
  'Analytics': 'bg-amber-500/20 text-amber-400',
}

export default function Missions() {
  const [missions, setMissions] = useState<Mission[]>([])
  const [filter, setFilter] = useState<string>('all')

  useEffect(() => {
    fetch('/api/missions')
      .then(r => r.json())
      .then(d => setMissions(d.missions || []))
      .catch(() => {})
  }, [])

  const categories = ['all', ...new Set(missions.map(m => m.category))]
  const filtered = filter === 'all' ? missions : missions.filter(m => m.category === filter)
  const completed = missions.filter(m => m.status === 'completed').length
  const total = missions.length

  return (
    <div className="max-w-5xl space-y-6">
      {/* Summary */}
      <div className="card p-5 flex items-center justify-between">
        <div>
          <h3 className="text-lg font-bold text-white">Mission Progress</h3>
          <p className="text-sm text-slate-400 mt-1">{completed} of {total} missions completed</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="h-2.5 w-48 bg-slate-700 rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-amber-500 to-orange-500 rounded-full progress-bar-animated"
              style={{ width: `${total > 0 ? (completed / total) * 100 : 0}%` }}
            />
          </div>
          <span className="text-sm font-semibold text-amber-400">{total > 0 ? Math.round((completed / total) * 100) : 0}%</span>
        </div>
      </div>

      {/* Category filter */}
      <div className="flex gap-2 flex-wrap">
        {categories.map(cat => (
          <button
            key={cat}
            onClick={() => setFilter(cat)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all border
              ${filter === cat
                ? 'bg-amber-500/20 text-amber-400 border-amber-500/30'
                : 'bg-slate-800 text-slate-400 border-slate-700 hover:border-slate-600'
              }`}
          >
            {cat === 'all' ? 'All Missions' : cat}
          </button>
        ))}
      </div>

      {/* Mission grid */}
      <div className="grid md:grid-cols-2 gap-4">
        {filtered.map(mission => {
          const Icon = ICON_MAP[mission.icon] || Target
          const done = mission.status === 'completed'
          return (
            <div
              key={mission.id}
              className={`card-hover p-5 relative overflow-hidden
                ${done ? 'border-green-500/30' : ''}`}
            >
              {done && (
                <div className="absolute top-0 right-0 w-24 h-24 bg-gradient-to-bl from-green-500/10 to-transparent" />
              )}

              <div className="flex items-start gap-4">
                <div className={`w-11 h-11 rounded-lg flex items-center justify-center shrink-0
                  ${done ? 'bg-green-500/20' : 'bg-slate-800'}`}>
                  <Icon className={`w-5.5 h-5.5 ${done ? 'text-green-400' : 'text-amber-400'}`} />
                </div>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <h4 className="font-semibold text-white text-sm">{mission.name}</h4>
                    {mission.award_type === 'repeatable' && (
                      <Repeat className="w-3.5 h-3.5 text-violet-400" />
                    )}
                  </div>
                  <p className="text-xs text-slate-400 mt-1 leading-relaxed">{mission.description}</p>

                  <div className="flex items-center justify-between mt-3">
                    <span className={`text-xs px-2 py-0.5 rounded-full ${CATEGORY_COLORS[mission.category] || 'bg-slate-700 text-slate-300'}`}>
                      {mission.category}
                    </span>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-bold text-amber-400">+{mission.points}</span>
                      <span className="text-xs text-slate-500">pts</span>
                    </div>
                  </div>
                </div>

                <div className="shrink-0 mt-0.5">
                  {done ? (
                    <CheckCircle2 className="w-5 h-5 text-green-400" />
                  ) : (
                    <Circle className="w-5 h-5 text-slate-600" />
                  )}
                </div>
              </div>

              {done && mission.completed_at && (
                <p className="text-xs text-green-400/60 mt-3 ml-15">
                  Completed {new Date(mission.completed_at).toLocaleDateString()}
                </p>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
