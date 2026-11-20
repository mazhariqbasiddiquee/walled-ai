import { Fragment, useState } from 'react'
import type { RequestStatus, ToolRequest } from '../../../../shared/types'
import { useLiveQuery } from '../../hooks'
import { dateTime, timeAgo } from '../../lib/format'
import { ArgumentsView, Button, EmptyState, ErrorNote, Field, PageHeader, StatusBadge } from '../ui'

const FILTERS: (RequestStatus | 'ALL')[] = ['ALL', 'APPROVED', 'REJECTED', 'EXPIRED', 'CANCELLED', 'PENDING']

export function HistoryPage() {
  const [filter, setFilter] = useState<RequestStatus | 'ALL'>('ALL')
  const [open, setOpen] = useState<string | null>(null)
  const { data, error, loading, reload } = useLiveQuery(
    () => window.guard.listRequests({ status: filter === 'ALL' ? undefined : filter, limit: 300 }),
    [filter],
    ['request.']
  )

  return (
    <>
      <PageHeader
        title="History"
        subtitle="Audit log of every intercepted tool call."
        actions={
          <Button variant="ghost" onClick={() => void reload()}>
            Refresh
          </Button>
        }
      />

      <div className="mb-4 flex flex-wrap gap-1.5">
        {FILTERS.map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
              filter === f ? 'bg-zinc-100 text-zinc-900' : 'bg-zinc-900 text-zinc-400 ring-1 ring-zinc-800 hover:text-zinc-200'
            }`}
          >
            {f === 'ALL' ? 'All' : f.charAt(0) + f.slice(1).toLowerCase()}
          </button>
        ))}
      </div>

      <ErrorNote message={error} />

      {!loading && data?.length === 0 ? (
        <EmptyState title="No requests yet" />
      ) : (
        <div className="overflow-hidden rounded-xl ring-1 ring-zinc-800">
          <table className="w-full text-left text-sm">
            <thead className="bg-zinc-900/80 text-xs text-zinc-500">
              <tr>
                <th className="px-4 py-2.5 font-medium">Time</th>
                <th className="px-4 py-2.5 font-medium">Server / tool</th>
                <th className="px-4 py-2.5 font-medium">Decision</th>
                <th className="px-4 py-2.5 font-medium">Decided by</th>
                <th className="px-4 py-2.5 font-medium">Result</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/80">
              {data?.map((r) => (
                <Fragment key={r.request_id}>
                  <tr
                    onClick={() => setOpen(open === r.request_id ? null : r.request_id)}
                    className="cursor-pointer bg-zinc-950/40 hover:bg-zinc-900/60"
                  >
                    <td className="px-4 py-2.5 whitespace-nowrap text-zinc-400" title={dateTime(r.created_at)}>
                      {timeAgo(r.created_at)}
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="text-zinc-200">{r.server}</span>
                      <span className="text-zinc-600"> / </span>
                      <code className="font-mono text-[13px] text-emerald-300">{r.tool}</code>
                    </td>
                    <td className="px-4 py-2.5">
                      <StatusBadge status={r.status} />
                    </td>
                    <td className="px-4 py-2.5 text-zinc-400">{r.decided_by ?? '—'}</td>
                    <td className="px-4 py-2.5">
                      <ResultCell r={r} />
                    </td>
                  </tr>
                  {open === r.request_id && (
                    <tr className="bg-zinc-900/40">
                      <td colSpan={5} className="px-4 py-4">
                        <Details r={r} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}

function ResultCell({ r }: { r: ToolRequest }) {
  if (!r.execution_status) return <span className="text-zinc-600">not executed</span>
  const ok = r.execution_status === 'success'
  return (
    <span className={ok ? 'text-emerald-300' : 'text-rose-300'}>
      {ok ? 'success' : 'error'}
      {r.duration_ms !== null && <span className="ml-1.5 text-zinc-500">{r.duration_ms} ms</span>}
    </span>
  )
}

function Details({ r }: { r: ToolRequest }) {
  return (
    <div className="grid grid-cols-[1fr_1fr] gap-6">
      <div>
        <h3 className="mb-2 text-xs font-semibold tracking-wider text-zinc-500 uppercase">Parameters</h3>
        <ArgumentsView args={r.arguments} />
      </div>
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <Field label="Request ID">
            <span className="selectable font-mono text-xs">{r.request_id}</span>
          </Field>
          <Field label="Created">{dateTime(r.created_at)}</Field>
          <Field label="Decided">{dateTime(r.decided_at)}</Field>
          <Field label="Reason">{r.decision_reason ?? '—'}</Field>
        </div>
        {r.result_preview && (
          <Field label="Result">
            <pre className="selectable max-h-48 overflow-auto rounded-lg bg-zinc-950 px-3 py-2 font-mono text-xs whitespace-pre-wrap text-zinc-200 ring-1 ring-zinc-800">
              {r.result_preview}
            </pre>
          </Field>
        )}
        {r.error && !r.result_preview && (
          <Field label="Error">
            <pre className="selectable rounded-lg bg-rose-500/10 px-3 py-2 font-mono text-xs whitespace-pre-wrap text-rose-200">
              {r.error}
            </pre>
          </Field>
        )}
      </div>
    </div>
  )
}
