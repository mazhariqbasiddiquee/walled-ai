import { useState } from 'react'
import type { ToolRequest } from '../../../../shared/types'
import { useLiveQuery, useNow } from '../../hooks'
import { classifyTool, errorMessage, formatCountdown, secondsLeft, timeAgo } from '../../lib/format'
import { ActionBadge, ArgumentsView, Button, EmptyState, ErrorNote, PageHeader } from '../ui'

export function PendingPage({ pending }: { pending: ToolRequest[] }) {
  const { data: stats } = useLiveQuery(() => window.guard.stats(), [], ['request.'])

  return (
    <>
      <PageHeader title="Pending requests" subtitle="Tool calls waiting for your decision. Nothing runs until you allow it." />

      <div className="mb-8 grid grid-cols-4 gap-3">
        <Stat label="Pending" value={pending.length} tone="text-amber-300" />
        <Stat label="Approved" value={stats?.APPROVED} tone="text-emerald-300" />
        <Stat label="Blocked" value={stats?.REJECTED} tone="text-rose-300" />
        <Stat label="Expired" value={stats ? stats.EXPIRED + stats.CANCELLED : undefined} tone="text-zinc-300" />
      </div>

      {pending.length === 0 ? (
        <EmptyState
          title="All clear"
          hint="When Claude calls a protected MCP tool, it appears here and an approval popup opens."
        />
      ) : (
        <div className="space-y-4">
          {pending.map((req) => (
            <PendingCard key={req.request_id} req={req} />
          ))}
        </div>
      )}
    </>
  )
}

function Stat({ label, value, tone }: { label: string; value: number | undefined; tone: string }) {
  return (
    <div className="rounded-xl bg-zinc-900/70 px-4 py-3 ring-1 ring-zinc-800">
      <div className="text-xs text-zinc-500">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${tone}`}>{value ?? '—'}</div>
    </div>
  )
}

function PendingCard({ req }: { req: ToolRequest }) {
  const now = useNow()
  const [remember, setRemember] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const left = secondsLeft(req.expires_at, now)

  async function decide(decision: 'allow' | 'block') {
    setBusy(true)
    setError(null)
    try {
      await window.guard.decide({ requestId: req.request_id, decision, remember })
    } catch (err) {
      setError(errorMessage(err))
      setBusy(false)
    }
  }

  return (
    <div className="rounded-xl bg-zinc-900/70 p-5 ring-1 ring-amber-500/30">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-medium text-zinc-100">{req.server}</span>
            <span className="text-zinc-600">/</span>
            <code className="font-mono text-sm text-emerald-300">{req.tool}</code>
          </div>
          <div className="mt-1 flex items-center gap-3 text-xs text-zinc-500">
            <span>{timeAgo(req.created_at)}</span>
            <span className="selectable font-mono">{req.request_id}</span>
          </div>
        </div>
        <div className="flex flex-col items-end gap-2">
          <ActionBadge kind={classifyTool(req.tool)} />
          {left !== null && (
            <span className={`text-xs ${left <= 15 ? 'text-rose-300' : 'text-zinc-400'}`}>
              auto-block in {formatCountdown(left)}
            </span>
          )}
        </div>
      </div>
      <ArgumentsView args={req.arguments} />
      <div className="mt-4 space-y-3">
        <ErrorNote message={error} />
        <div className="flex items-center justify-between">
          <label className="flex cursor-pointer items-center gap-2 text-xs text-zinc-400">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
              className="size-3.5 accent-emerald-500"
            />
            Remember this decision
          </label>
          <div className="flex gap-2">
            <Button variant="danger" disabled={busy} onClick={() => decide('block')}>
              Block
            </Button>
            <Button variant="primary" disabled={busy} onClick={() => decide('allow')}>
              Allow
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
