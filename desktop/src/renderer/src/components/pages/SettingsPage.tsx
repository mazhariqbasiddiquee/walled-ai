import { useEffect, useState } from 'react'
import type { PolicyAction } from '../../../../shared/types'
import { useLiveQuery } from '../../hooks'
import { errorMessage } from '../../lib/format'
import { Button, ErrorNote, PageHeader } from '../ui'

const input =
  'w-full rounded-lg bg-zinc-950 px-3 py-2 text-sm text-zinc-100 ring-1 ring-zinc-800 outline-none focus:ring-emerald-500/60'

export function SettingsPage({ connected, daemonUrl }: { connected: boolean; daemonUrl: string }) {
  const { data: settings, error } = useLiveQuery(() => window.guard.getSettings(), [], ['settings.'])
  const [timeout, setTimeoutValue] = useState(60)
  const [defaultAction, setDefaultAction] = useState<PolicyAction>('ask')
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    if (settings) {
      setTimeoutValue(settings.approval_timeout_seconds)
      setDefaultAction(settings.default_action)
    }
  }, [settings])

  async function save() {
    setSaving(true)
    setMessage(null)
    try {
      await window.guard.updateSettings({ approval_timeout_seconds: timeout, default_action: defaultAction })
      setMessage({ ok: true, text: 'Saved.' })
    } catch (err) {
      setMessage({ ok: false, text: errorMessage(err) })
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <PageHeader title="Settings" subtitle="Stored by the daemon in ~/.mcp-guard/config.json." />
      <ErrorNote message={error} />

      <div className="max-w-xl space-y-6">
        <section className="space-y-5 rounded-xl bg-zinc-900/70 p-5 ring-1 ring-zinc-800">
          <label className="block">
            <div className="text-sm font-medium text-zinc-200">Approval timeout</div>
            <div className="mb-2 text-xs text-zinc-500">
              Unanswered requests are blocked after this many seconds. Keep it below your MCP client&apos;s own tool timeout.
            </div>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min={5}
                max={3600}
                value={timeout}
                onChange={(e) => setTimeoutValue(Number(e.target.value))}
                className={`${input} w-32`}
              />
              <span className="text-sm text-zinc-400">seconds</span>
            </div>
          </label>

          <label className="block">
            <div className="text-sm font-medium text-zinc-200">When no rule matches</div>
            <div className="mb-2 text-xs text-zinc-500">"Ask me" is the safe default.</div>
            <select
              value={defaultAction}
              onChange={(e) => setDefaultAction(e.target.value as PolicyAction)}
              className={`${input} w-48`}
            >
              <option value="ask">Ask me</option>
              <option value="block">Block</option>
              <option value="allow">Allow (not recommended)</option>
            </select>
          </label>

          <div className="flex items-center gap-3">
            <Button variant="primary" disabled={!connected || saving} onClick={() => void save()}>
              {saving ? 'Saving…' : 'Save settings'}
            </Button>
            {message && <span className={`text-sm ${message.ok ? 'text-emerald-300' : 'text-rose-300'}`}>{message.text}</span>}
          </div>
        </section>

        <section className="space-y-2 rounded-xl bg-zinc-900/70 p-5 text-sm ring-1 ring-zinc-800">
          <div className="font-medium text-zinc-200">Daemon</div>
          <div className="text-zinc-400">
            Status:{' '}
            <span className={connected ? 'text-emerald-300' : 'text-rose-300'}>{connected ? 'connected' : 'offline'}</span>
          </div>
          <div className="text-zinc-400">
            Address: <code className="selectable font-mono text-zinc-300">{daemonUrl || '—'}</code>
          </div>
          <div className="pt-2 text-xs text-zinc-500">
            Protect an MCP server by prefixing its command in Claude&apos;s config with{' '}
            <code className="selectable font-mono text-zinc-300">mcp-guard proxy --name &lt;name&gt; --</code>
          </div>
        </section>
      </div>
    </>
  )
}
