import type { RequestStatus } from '../../../shared/types'

export function timeAgo(iso: string | null): string {
  if (!iso) return '—'
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 5) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return new Date(iso).toLocaleDateString()
}

export function dateTime(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : '—'
}

export function secondsLeft(expiresAt: string | null, now: number): number | null {
  if (!expiresAt) return null
  return Math.max(0, Math.ceil((new Date(expiresAt).getTime() - now) / 1000))
}

export function formatCountdown(s: number): string {
  const m = Math.floor(s / 60)
  return `${m}:${String(s % 60).padStart(2, '0')}`
}

export type ActionKind = { label: string; tone: 'danger' | 'warn' | 'info' }

const WRITE =
  /(write|create|delete|remove|rm|move|rename|edit|update|patch|put|post|exec|run|shell|command|send|insert|drop|kill|install|upload|push|commit|deploy)/i
const READ = /(read|get|list|search|find|fetch|query|view|show|describe|stat|echo|ls|tree|info)/i

/** Heuristic hint for the reviewer; the decision is always the user's. */
export function classifyTool(tool: string): ActionKind {
  if (WRITE.test(tool)) return { label: 'Modifies data or runs something', tone: 'danger' }
  if (READ.test(tool)) return { label: 'Read-only operation', tone: 'info' }
  return { label: 'Unknown operation', tone: 'warn' }
}

export const STATUS_STYLE: Record<RequestStatus, string> = {
  PENDING: 'bg-amber-500/15 text-amber-300 ring-amber-500/30',
  APPROVED: 'bg-emerald-500/15 text-emerald-300 ring-emerald-500/30',
  REJECTED: 'bg-rose-500/15 text-rose-300 ring-rose-500/30',
  EXPIRED: 'bg-zinc-500/15 text-zinc-300 ring-zinc-500/30',
  CANCELLED: 'bg-zinc-500/15 text-zinc-400 ring-zinc-500/30'
}

export function errorMessage(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err)
  // Strip Electron's "Error invoking remote method 'x': Error: " prefix.
  return msg.replace(/^Error invoking remote method '[^']+': (\w*Error: )?/, '')
}
