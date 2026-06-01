import { useEffect, useState } from 'react'
import { Trophy, Medal, Crown, ChevronUp } from 'lucide-react'
import type { LeaderboardEntry } from '../types'

const PERIOD_OPTIONS = [
  { id: 'all', label: 'All Time' },
  { id: 'weekly', label: 'This Week' },
  { id: 'monthly', label: 'This Month' },
]

const LEVEL_BADGE: Record<string, string> = {
  Bronze: 'bg-amber-800/30 text-amber-600 border-amber-700/40',
  Silver: 'bg-slate-600/30 text-slate-400 border-slate-500/40',
  Gold: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/40',
  Platinum: 'bg-violet-500/20 text-violet-400 border-violet-500/40',
  Elite: 'bg-orange-500/20 text-orange-400 border-orange-500/40',
}

function RankIcon({ rank }: { rank: number }) {
  if (rank === 1) return <Crown className="w-5 h-5 text-yellow-400" />
  if (rank === 2) return <Medal className="w-5 h-5 text-slate-400" />
  if (rank === 3) return <Medal className="w-5 h-5 text-amber-700" />
  return <span className="text-sm text-slate-500 font-mono w-5 text-center">{rank}</span>
}

function getRankBg(rank: number): string {
  if (rank === 1) return 'bg-gradient-to-r from-yellow-500/10 to-transparent border-yellow-500/30'
  if (rank === 2) return 'bg-gradient-to-r from-slate-500/10 to-transparent border-slate-500/20'
  if (rank === 3) return 'bg-gradient-to-r from-amber-700/10 to-transparent border-amber-700/20'
  return 'border-slate-800/50'
}

export default function Leaderboard() {
  const [entries, setEntries] = useState<LeaderboardEntry[]>([])
  const [period, setPeriod] = useState('all')

  useEffect(() => {
    fetch(`/api/leaderboard?period=${period}`)
      .then(r => r.json())
      .then(d => setEntries(d.leaderboard || []))
      .catch(() => {})
  }, [period])

  const getPoints = (e: LeaderboardEntry) => {
    if (period === 'weekly') return e.weekly_points
    if (period === 'monthly') return e.monthly_points
    return e.total_points
  }

  const getRank = (e: LeaderboardEntry) => {
    if (period === 'weekly') return e.weekly_rank
    if (period === 'monthly') return e.monthly_rank
    return e.all_time_rank
  }

  const sorted = [...entries].sort((a, b) => getRank(a) - getRank(b))

  return (
    <div className="max-w-3xl space-y-6">
      {/* Period tabs */}
      <div className="flex gap-2">
        {PERIOD_OPTIONS.map(opt => (
          <button
            key={opt.id}
            onClick={() => setPeriod(opt.id)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-all border
              ${period === opt.id
                ? 'bg-amber-500/15 text-amber-400 border-amber-500/30'
                : 'bg-slate-800 text-slate-400 border-slate-700 hover:border-slate-600'
              }`}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {/* Top 3 podium */}
      {sorted.length >= 3 && (
        <div className="grid grid-cols-3 gap-4">
          {[sorted[1], sorted[0], sorted[2]].map((entry, i) => {
            const rank = [2, 1, 3][i]
            const pts = getPoints(entry)
            return (
              <div
                key={entry.user_id}
                className={`card p-5 text-center ${rank === 1 ? 'ring-2 ring-yellow-500/30 -mt-2' : 'mt-2'}`}
              >
                <div className="flex justify-center mb-3">
                  <div className={`w-14 h-14 rounded-full flex items-center justify-center text-lg font-bold
                    ${rank === 1 ? 'bg-yellow-500/20 text-yellow-400 ring-2 ring-yellow-500/40' :
                      rank === 2 ? 'bg-slate-600/30 text-slate-400' :
                        'bg-amber-800/30 text-amber-700'}`}
                  >
                    {(entry.display_name || '?')[0].toUpperCase()}
                  </div>
                </div>
                <RankIcon rank={rank} />
                <p className="font-semibold text-white text-sm mt-2 truncate">{entry.display_name}</p>
                <p className="text-amber-400 font-bold text-lg mt-1">{pts}</p>
                <p className="text-xs text-slate-500">points</p>
                <span className={`inline-block mt-2 text-xs px-2 py-0.5 rounded-full border ${LEVEL_BADGE[entry.level] || LEVEL_BADGE.Bronze}`}>
                  {entry.level}
                </span>
              </div>
            )
          })}
        </div>
      )}

      {/* Full list */}
      <div className="card p-0 divide-y divide-slate-800/50">
        <div className="grid grid-cols-12 gap-2 px-5 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">
          <div className="col-span-1">Rank</div>
          <div className="col-span-5">User</div>
          <div className="col-span-2 text-center">Level</div>
          <div className="col-span-2 text-right">Points</div>
          <div className="col-span-2 text-right">Trend</div>
        </div>
        {sorted.length === 0 ? (
          <div className="p-6 text-center text-slate-500 text-sm">No leaderboard data yet</div>
        ) : (
          sorted.map(entry => {
            const rank = getRank(entry)
            const pts = getPoints(entry)
            return (
              <div
                key={entry.user_id}
                className={`grid grid-cols-12 gap-2 px-5 py-3.5 items-center border-l-2 transition hover:bg-slate-800/30 ${getRankBg(rank)}`}
              >
                <div className="col-span-1 flex items-center">
                  <RankIcon rank={rank} />
                </div>
                <div className="col-span-5 flex items-center gap-3">
                  <div className="w-8 h-8 rounded-full bg-slate-700 flex items-center justify-center text-xs font-bold text-amber-400 shrink-0">
                    {(entry.display_name || '?')[0].toUpperCase()}
                  </div>
                  <span className="text-sm font-medium text-white truncate">{entry.display_name}</span>
                </div>
                <div className="col-span-2 text-center">
                  <span className={`text-xs px-2 py-0.5 rounded-full border ${LEVEL_BADGE[entry.level] || LEVEL_BADGE.Bronze}`}>
                    {entry.level}
                  </span>
                </div>
                <div className="col-span-2 text-right">
                  <span className="text-sm font-bold text-amber-400">{pts}</span>
                </div>
                <div className="col-span-2 text-right">
                  {entry.weekly_points > 0 && (
                    <span className="text-xs text-green-400 flex items-center justify-end gap-0.5">
                      <ChevronUp className="w-3 h-3" />+{entry.weekly_points} this week
                    </span>
                  )}
                </div>
              </div>
            )
          })
        )}
      </div>

      {/* Swag banner */}
      <div className="card p-5 bg-gradient-to-r from-violet-600/20 to-cyan-600/20 border-violet-500/20">
        <div className="flex items-center gap-4">
          <Trophy className="w-8 h-8 text-violet-400 shrink-0" />
          <div>
            <h4 className="font-semibold text-white">Weekly Swag Awards</h4>
            <p className="text-sm text-slate-400 mt-0.5">
              Top performers each week receive exclusive Databricks swag. Keep climbing the leaderboard!
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
