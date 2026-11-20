import { useState, type FormEvent } from 'react'
import type { PolicyAction } from '../../../../shared/types'
import { useLiveQuery } from '../../hooks'
import { errorMessage, timeAgo } from '../../lib/format'
import { Button, EmptyState, ErrorNote, PageHeader } from '../ui'

const ACTION_STYLE: Record<PolicyAction, string> = {
  allow: 'bg-emerald-500/15 text-emerald-300',
  block: 'bg-rose-500/15 text-rose-300',
  ask: 'bg-amber-500/15 text-amber-300'
}

const input =
  'w-full rounded-lg bg-zinc-950 px-3 py-2 text-sm text-zinc-100 ring-1 ring-zinc-800 outline-none placeholder:text-zinc-600 focus:ring-emerald-500/60'

export function RulesPage() {
  const { data: rules, error, reload } = useLiveQuery(() => window.guard.listRules(), [], ['rule.'])
  const [server, setServer] = useState('*')
  const [tool, setTool] = useState('')
  const [action, setAction] = useState<PolicyAction>('allow')
  const [note, setNote] = useState('')
  const [formError, setFormError] = useState<string | null>(null)

  async function add(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    try {
      await window.guard.addRule({ server: server || '*', tool: tool || '*', action, note: note || undefined })
      setTool('')
      setNote('')
      void reload()
    } catch (err) {
      setFormError(errorMessage(err))
    }
  }

  async function remove(id: number) {
    try {
      await window.guard.deleteRule(id)
      void reload()
    } catch (err) {
      setFormError(errorMessage(err))
    }
  }

  return (
    <>
      <PageHeader
        title="Rules"
        subtitle="Decide some tool calls automatically. The newest matching rule wins; otherwise the default action applies."
      />

      <form onSubmit={add} className="mb-6 rounded-xl bg-zinc-900/70 p-4 ring-1 ring-zinc-800">
        <div className="grid grid-cols-[1fr_1fr_140px_1.3fr_auto] items-end gap-3">
          <label className="text-xs text-zinc-400">
            Server
            <input className={`${input} mt-1`} value={server} onChange={(e) => setServer(e.target.value)} placeholder="*" />
          </label>
          <label className="text-xs text-zinc-400">
            Tool
            <input className={`${input} mt-1`} value={tool} onChange={(e) => setTool(e.target.value)} placeholder="read_* or *" />
          </label>
          <label className="text-xs text-zinc-400">
            Action
            <select className={`${input} mt-1`} value={action} onChange={(e) => setAction(e.target.value as PolicyAction)}>
              <option value="allow">Allow</option>
              <option value="block">Block</option>
              <option value="ask">Ask me</option>
            </select>
          </label>
          <label className="text-xs text-zinc-400">
            Note
            <input className={`${input} mt-1`} value={note} onChange={(e) => setNote(e.target.value)} placeholder="optional" />
          </label>
          <Button type="submit" variant="primary">
            Add rule
          </Button>
        </div>
        <p className="mt-2 text-xs text-zinc-500">
          Patterns support <code className="font-mono">*</code> and <code className="font-mono">?</code>, e.g. server{' '}
          <code className="font-mono">filesystem</code>, tool <code className="font-mono">delete_*</code> → Block.
        </p>
        <div className="mt-3">
          <ErrorNote message={formError} />
        </div>
      </form>

      <ErrorNote message={error} />

      {rules?.length === 0 ? (
        <EmptyState title="No rules yet" hint='Every tool call will ask you. Tick "Remember" in a popup to add one quickly.' />
      ) : (
        <div className="overflow-hidden rounded-xl ring-1 ring-zinc-800">
          <table className="w-full text-left text-sm">
            <thead className="bg-zinc-900/80 text-xs text-zinc-500">
              <tr>
                <th className="px-4 py-2.5 font-medium">Server</th>
                <th className="px-4 py-2.5 font-medium">Tool</th>
                <th className="px-4 py-2.5 font-medium">Action</th>
                <th className="px-4 py-2.5 font-medium">Note</th>
                <th className="px-4 py-2.5 font-medium">Added</th>
                <th />
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/80 bg-zinc-950/40">
              {rules?.map((r) => (
                <tr key={r.id}>
                  <td className="px-4 py-2.5 font-mono text-[13px] text-zinc-200">{r.server}</td>
                  <td className="px-4 py-2.5 font-mono text-[13px] text-zinc-200">{r.tool}</td>
                  <td className="px-4 py-2.5">
                    <span className={`rounded-md px-2 py-0.5 text-xs font-medium ${ACTION_STYLE[r.action]}`}>{r.action}</span>
                  </td>
                  <td className="px-4 py-2.5 text-zinc-400">{r.note ?? '—'}</td>
                  <td className="px-4 py-2.5 text-zinc-500">{timeAgo(r.created_at)}</td>
                  <td className="px-4 py-2.5 text-right">
                    <Button variant="ghost" className="!px-2 !py-1 text-xs" onClick={() => void remove(r.id)}>
                      Delete
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
