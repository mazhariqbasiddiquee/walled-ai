import { useState } from 'react'
import { useGuardState } from '../hooks'
import { HistoryPage } from './pages/HistoryPage'
import { PendingPage } from './pages/PendingPage'
import { RulesPage } from './pages/RulesPage'
import { SettingsPage } from './pages/SettingsPage'
import { ShieldIcon } from './ui'

type Tab = 'pending' | 'history' | 'rules' | 'settings'

export function MainWindow() {
  const state = useGuardState()
  const [tab, setTab] = useState<Tab>('pending')

  const tabs: { id: Tab; label: string; badge?: number }[] = [
    { id: 'pending', label: 'Pending', badge: state.pending.length },
    { id: 'history', label: 'History' },
    { id: 'rules', label: 'Rules' },
    { id: 'settings', label: 'Settings' }
  ]

  return (
    <div className="flex h-full">
      <aside className="drag flex w-56 shrink-0 flex-col border-r border-zinc-800/80 bg-zinc-950/60 px-3 pt-12 pb-4">
        <div className="mb-6 flex items-center gap-2 px-2">
          <div className="flex size-8 items-center justify-center rounded-lg bg-emerald-500/15 text-emerald-400">
            <ShieldIcon />
          </div>
          <div>
            <div className="text-sm font-semibold text-zinc-100">MCP Guard</div>
            <div className="text-[11px] text-zinc-500">Tool call approvals</div>
          </div>
        </div>
        <nav className="space-y-0.5">
          {tabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-sm transition-colors ${
                tab === t.id ? 'bg-zinc-800/80 text-zinc-50' : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200'
              }`}
            >
              {t.label}
              {!!t.badge && (
                <span className="rounded-full bg-amber-500 px-1.5 text-[11px] font-semibold text-amber-950">{t.badge}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="mt-auto rounded-lg bg-zinc-900/70 px-3 py-2.5 ring-1 ring-zinc-800">
          <div className="flex items-center gap-2 text-xs font-medium">
            <span className={`size-2 rounded-full ${state.connected ? 'bg-emerald-400' : 'bg-rose-400'}`} />
            <span className={state.connected ? 'text-emerald-300' : 'text-rose-300'}>
              {state.connected ? 'Protected' : 'Daemon offline'}
            </span>
          </div>
          <div className="mt-1 truncate text-[11px] text-zinc-500" title={state.error ?? state.daemonUrl}>
            {state.connected ? state.daemonUrl : (state.error ?? 'Connecting…')}
          </div>
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col">
        <div className="drag h-10 shrink-0" />
        <div className="min-h-0 flex-1 overflow-y-auto px-8 pb-8">
          {!state.connected && (
            <div className="mb-6 rounded-xl bg-rose-500/10 px-4 py-3 text-sm text-rose-200 ring-1 ring-rose-500/25">
              <div className="font-medium">Not connected to the MCP Guard daemon.</div>
              <div className="mt-1 text-rose-300/80">
                Start it in a terminal with <code className="selectable font-mono">mcp-guard daemon</code>. While it is
                offline, every protected tool call is blocked.
              </div>
            </div>
          )}
          {tab === 'pending' && <PendingPage pending={state.pending} />}
          {tab === 'history' && <HistoryPage />}
          {tab === 'rules' && <RulesPage />}
          {tab === 'settings' && <SettingsPage connected={state.connected} daemonUrl={state.daemonUrl} />}
        </div>
      </main>
    </div>
  )
}
