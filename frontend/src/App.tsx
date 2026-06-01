import { useState, useEffect, useCallback } from 'react'
import {
  LayoutDashboard, Target, Trophy, Settings, Bell, ChevronRight,
  Sparkles,
} from 'lucide-react'
import Dashboard from './components/Dashboard'
import Missions from './components/Missions'
import Leaderboard from './components/Leaderboard'
import AdminPanel from './components/AdminPanel'
import type { Page, UserProfile, Notification } from './types'

const NAV_ITEMS: { id: Page; label: string; icon: typeof LayoutDashboard }[] = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { id: 'missions', label: 'Missions', icon: Target },
  { id: 'leaderboard', label: 'Leaderboard', icon: Trophy },
  { id: 'admin', label: 'Admin', icon: Settings },
]

const LEVEL_COLORS: Record<string, string> = {
  Bronze: 'text-amber-700',
  Silver: 'text-slate-400',
  Gold: 'text-yellow-400',
  Platinum: 'text-violet-400',
  Elite: 'text-orange-400',
}

const LEVEL_BG: Record<string, string> = {
  Bronze: 'from-amber-900/40 to-amber-800/20',
  Silver: 'from-slate-600/40 to-slate-500/20',
  Gold: 'from-yellow-600/40 to-yellow-500/20',
  Platinum: 'from-violet-600/40 to-violet-500/20',
  Elite: 'from-orange-600/40 to-orange-500/20',
}

export default function App() {
  const [page, setPage] = useState<Page>('dashboard')
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [notifications, setNotifications] = useState<Notification[]>([])
  const [showNotifs, setShowNotifs] = useState(false)

  const fetchProfile = useCallback(async () => {
    try {
      const res = await fetch('/api/profile')
      if (res.ok) setProfile(await res.json())
    } catch { /* silent */ }
  }, [])

  const fetchNotifications = useCallback(async () => {
    try {
      const res = await fetch('/api/notifications')
      if (res.ok) {
        const data = await res.json()
        setNotifications(data.notifications || [])
      }
    } catch { /* silent */ }
  }, [])

  useEffect(() => {
    fetchProfile()
    fetchNotifications()
  }, [fetchProfile, fetchNotifications])

  const level = profile?.level || 'Bronze'

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside className="w-64 bg-slate-900/90 border-r border-slate-800 flex flex-col shrink-0">
        {/* Logo */}
        <div className="p-5 border-b border-slate-800">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-amber-500 to-orange-600 flex items-center justify-center">
              <Sparkles className="w-6 h-6 text-white" />
            </div>
            <div>
              <h1 className="font-bold text-lg text-white leading-tight">Databricks</h1>
              <p className="text-amber-500 text-sm font-semibold -mt-0.5">Quest</p>
            </div>
          </div>
        </div>

        {/* User card */}
        {profile && (
          <div className={`mx-3 mt-4 p-3 rounded-lg bg-gradient-to-r ${LEVEL_BG[level]} border border-slate-700/50`}>
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-full bg-slate-700 flex items-center justify-center text-sm font-bold text-amber-400">
                {(profile.display_name || '?')[0].toUpperCase()}
              </div>
              <div className="min-w-0">
                <p className="text-sm font-medium text-white truncate">{profile.display_name}</p>
                <p className={`text-xs font-semibold ${LEVEL_COLORS[level]}`}>{level}</p>
              </div>
            </div>
            <div className="mt-2">
              <div className="flex justify-between text-xs text-slate-400 mb-1">
                <span>{profile.total_points} pts</span>
                <span>{profile.level_progress?.level_ceiling} pts</span>
              </div>
              <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-amber-500 to-orange-500 rounded-full progress-bar-animated"
                  style={{ width: `${profile.level_progress?.progress_pct || 0}%` }}
                />
              </div>
            </div>
          </div>
        )}

        {/* Navigation */}
        <nav className="flex-1 p-3 space-y-1 mt-2">
          {NAV_ITEMS.map(item => {
            const active = page === item.id
            const Icon = item.icon
            return (
              <button
                key={item.id}
                onClick={() => setPage(item.id)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all
                  ${active
                    ? 'bg-amber-500/15 text-amber-400 border border-amber-500/20'
                    : 'text-slate-400 hover:bg-slate-800 hover:text-white border border-transparent'
                  }`}
              >
                <Icon className="w-4.5 h-4.5" />
                {item.label}
                {active && <ChevronRight className="w-4 h-4 ml-auto" />}
              </button>
            )
          })}
        </nav>

        {/* Footer */}
        <div className="p-4 border-t border-slate-800 text-xs text-slate-500">
          Powered by Databricks System Tables
        </div>
      </aside>

      {/* Main area */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Top bar */}
        <header className="h-14 border-b border-slate-800 flex items-center justify-between px-6 shrink-0 bg-slate-900/50">
          <h2 className="text-lg font-semibold text-white capitalize">{page}</h2>
          <div className="flex items-center gap-4">
            {profile && (
              <div className="flex items-center gap-2 text-sm">
                <span className="text-amber-400 font-bold">{profile.total_points}</span>
                <span className="text-slate-500">points</span>
              </div>
            )}
            <div className="relative">
              <button
                onClick={() => setShowNotifs(!showNotifs)}
                className="relative p-2 rounded-lg hover:bg-slate-800 transition"
              >
                <Bell className="w-5 h-5 text-slate-400" />
                {notifications.length > 0 && (
                  <span className="absolute top-1 right-1 w-2 h-2 bg-amber-500 rounded-full" />
                )}
              </button>
              {showNotifs && (
                <div className="absolute right-0 mt-2 w-80 card p-0 z-50 max-h-96 overflow-y-auto shadow-2xl">
                  <div className="p-3 border-b border-slate-700 font-semibold text-sm">Notifications</div>
                  {notifications.length === 0 ? (
                    <div className="p-4 text-sm text-slate-500">No notifications yet</div>
                  ) : (
                    notifications.slice(0, 10).map((n, i) => (
                      <div key={i} className="p-3 border-b border-slate-800/50 hover:bg-slate-800/50">
                        <p className="text-sm font-medium text-white">{n.title}</p>
                        <p className="text-xs text-slate-400 mt-0.5">{n.message}</p>
                        {n.points > 0 && (
                          <span className="inline-block mt-1 text-xs font-semibold text-amber-400">+{n.points} pts</span>
                        )}
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto p-6 bg-slate-950">
          {page === 'dashboard' && <Dashboard profile={profile} onRefresh={fetchProfile} />}
          {page === 'missions' && <Missions />}
          {page === 'leaderboard' && <Leaderboard />}
          {page === 'admin' && <AdminPanel />}
        </main>
      </div>
    </div>
  )
}
