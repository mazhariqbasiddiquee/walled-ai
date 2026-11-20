import { useEffect, useState } from 'react'
import type { ToolRequest } from '../../../shared/types'
import { useGuardState, useNow } from '../hooks'
import { classifyTool, errorMessage, formatCountdown, secondsLeft } from '../lib/format'
import { ActionBadge, ArgumentsView, Button, ErrorNote, Field, ShieldIcon } from './ui'

/** The always-on-top window that appears when a tool call needs a decision. */
export function ApprovalPopup() {
  const { pending, connected } = useGuardState()
  const current: ToolRequest | undefined = pending[0] // oldest first

  return (
    <div className="flex h-full flex-col">
      <header className="drag flex h-12 shrink-0 items-center justify-end gap-2 border-b border-zinc-800/80 px-4">
        <span className="flex items-center gap-1.5 text-xs font-medium text-zinc-400">
          <ShieldIcon className="size-4 text-emerald-400" /> MCP Guard
        </span>
        {pending.length > 1 && (
          <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-medium text-amber-300">
            1 of {pending.length}
          </span>
        )}
      </header>
      {current ? (
        <RequestCard key={current.request_id} req={current} />
      ) : (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center">
          <ShieldIcon className="size-10 text-zinc-700" />
          <p className="text-sm text-zinc-400">{connected ? 'No requests waiting.' : 'Not connected to the daemon.'}</p>
        </div>
      )}
    </div>
  )
}

function RequestCard({ req }: { req: ToolRequest }) {
  const now = useNow()
  const [remember, setRemember] = useState(false)
  const [busy, setBusy] = useState<'allow' | 'block' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const left = secondsLeft(req.expires_at, now)
  const kind = classifyTool(req.tool)

  // Esc blocks: the safe default. There is deliberately no keyboard shortcut for Allow.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) void decide('block')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  async function decide(decision: 'allow' | 'block') {
    setBusy(decision)
    setError(null)
    try {
      await window.guard.decide({ requestId: req.request_id, decision, remember })
      // The daemon broadcasts request.decided; the queue advances automatically.
    } catch (err) {
      setError(errorMessage(err))
      setBusy(null)
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 pt-5 pb-4">
        <div>
          <div className="mb-2 inline-flex items-center gap-1.5 rounded-full bg-amber-500/15 px-2.5 py-1 text-xs font-semibold text-amber-300">
            <span className="size-1.5 animate-pulse rounded-full bg-amber-400" />
            Action required
          </div>
          <h1 className="text-lg font-semibold text-zinc-50">Permission request</h1>
          <p className="mt-1 text-sm text-zinc-400">Claude is requesting permission to execute an MCP tool.</p>
        </div>

        <div className="grid grid-cols-2 gap-4 rounded-xl bg-zinc-900/70 p-4 ring-1 ring-zinc-800">
          <Field label="MCP server">
            <span className="font-medium">{req.server}</span>
          </Field>
          <Field label="Tool">
            <code className="selectable font-mono text-[13px] text-emerald-300">{req.tool}</code>
          </Field>
          <div className="col-span-2">
            <Field label="Action">
              <ActionBadge kind={kind} />
            </Field>
          </div>
        </div>

        <div>
          <h2 className="mb-2 text-sm font-semibold text-zinc-200">Request parameters</h2>
          <ArgumentsView args={req.arguments} />
        </div>

        <ErrorNote message={error} />
      </div>

      <footer className="shrink-0 space-y-3 border-t border-zinc-800/80 bg-zinc-950/60 px-6 py-4">
        <div className="flex items-center justify-between text-xs text-zinc-400">
          <label className="flex cursor-pointer items-center gap-2">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
              className="size-3.5 accent-emerald-500"
            />
            Remember for <span className="font-mono text-zinc-300">{req.server}/{req.tool}</span>
          </label>
          {left !== null && (
            <span className={left <= 15 ? 'font-medium text-rose-300' : ''}>
              Auto-block in {formatCountdown(left)}
            </span>
          )}
        </div>
        <p className="text-xs text-zinc-500">Review the request carefully before allowing it.</p>
        <div className="grid grid-cols-2 gap-3">
          <Button variant="danger" disabled={!!busy} onClick={() => decide('block')}>
            {busy === 'block' ? 'Blocking…' : 'Block'}
            <kbd className="rounded bg-white/15 px-1 text-[10px]">esc</kbd>
          </Button>
          <Button variant="primary" disabled={!!busy} onClick={() => decide('allow')}>
            {busy === 'allow' ? 'Allowing…' : 'Allow'}
          </Button>
        </div>
      </footer>
    </div>
  )
}
