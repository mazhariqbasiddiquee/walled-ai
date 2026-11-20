import type { ButtonHTMLAttributes, ReactNode } from 'react'
import type { RequestStatus } from '../../../shared/types'
import { STATUS_STYLE, type ActionKind } from '../lib/format'

export function StatusBadge({ status }: { status: RequestStatus }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${STATUS_STYLE[status]}`}>
      {status.toLowerCase()}
    </span>
  )
}

const TONE: Record<ActionKind['tone'], string> = {
  danger: 'bg-rose-500/10 text-rose-300 ring-rose-500/25',
  warn: 'bg-amber-500/10 text-amber-300 ring-amber-500/25',
  info: 'bg-sky-500/10 text-sky-300 ring-sky-500/25'
}

export function ActionBadge({ kind }: { kind: ActionKind }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset ${TONE[kind.tone]}`}>
      <span className="size-1.5 rounded-full bg-current" />
      {kind.label}
    </span>
  )
}

type Variant = 'primary' | 'danger' | 'ghost' | 'subtle'
const VARIANT: Record<Variant, string> = {
  primary: 'bg-emerald-500 text-emerald-950 hover:bg-emerald-400',
  danger: 'bg-rose-500/90 text-white hover:bg-rose-500',
  subtle: 'bg-zinc-800 text-zinc-100 hover:bg-zinc-700',
  ghost: 'text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800/60'
}

export function Button({
  variant = 'subtle',
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${VARIANT[variant]} ${className}`}
    />
  )
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-[11px] font-medium tracking-wider text-zinc-500 uppercase">{label}</div>
      <div className="text-sm text-zinc-100">{children}</div>
    </div>
  )
}

/** Shows tool arguments as labelled values; long strings stay scrollable. */
export function ArgumentsView({ args }: { args: Record<string, unknown> }) {
  const entries = Object.entries(args)
  if (entries.length === 0) return <p className="text-sm text-zinc-500 italic">No parameters</p>
  return (
    <div className="space-y-3">
      {entries.map(([key, value]) => (
        <div key={key}>
          <div className="mb-1 text-xs font-medium text-zinc-400">{key}</div>
          <pre className="selectable max-h-40 overflow-auto rounded-lg bg-zinc-950 px-3 py-2 font-mono text-xs leading-relaxed break-all whitespace-pre-wrap text-zinc-200 ring-1 ring-zinc-800">
            {typeof value === 'string' ? value : JSON.stringify(value, null, 2)}
          </pre>
        </div>
      ))}
    </div>
  )
}

export function ErrorNote({ message }: { message: string | null }) {
  if (!message) return null
  return <div className="rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-300 ring-1 ring-rose-500/25">{message}</div>
}

export function EmptyState({ title, hint }: { title: string; hint?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-zinc-800 px-6 py-16 text-center">
      <div className="text-sm font-medium text-zinc-300">{title}</div>
      {hint && <div className="mt-1 max-w-md text-sm text-zinc-500">{hint}</div>}
    </div>
  )
}

export function ShieldIcon({ className = 'size-5' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className={className}>
      <path d="M12 3 4.5 6v5.5c0 4.6 3.1 8.4 7.5 9.5 4.4-1.1 7.5-4.9 7.5-9.5V6L12 3Z" strokeLinejoin="round" />
      <path d="m9 12 2 2 4-4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex items-end justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold text-zinc-50">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-zinc-400">{subtitle}</p>}
      </div>
      {actions}
    </div>
  )
}
