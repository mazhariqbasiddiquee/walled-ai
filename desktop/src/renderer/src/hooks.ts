import { useCallback, useEffect, useState } from 'react'
import type { GuardState } from '../../shared/types'
import { errorMessage } from './lib/format'

const INITIAL: GuardState = { connected: false, daemonUrl: '', error: null, pending: [] }

/** Live connection state + pending queue, pushed from the main process. */
export function useGuardState(): GuardState {
  const [state, setState] = useState<GuardState>(INITIAL)
  useEffect(() => {
    void window.guard.getState().then(setState)
    return window.guard.onState(setState)
  }, [])
  return state
}

/** Re-render once per second (for countdowns and "x ago" labels). */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(id)
  }, [intervalMs])
  return now
}

/**
 * Load data from the daemon, and reload whenever a daemon event arrives
 * (optionally filtered by event name prefix).
 */
export function useLiveQuery<T>(load: () => Promise<T>, deps: unknown[], eventPrefix?: string[]) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const reload = useCallback(load, deps)

  const run = useCallback(async () => {
    try {
      setData(await reload())
      setError(null)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [reload])

  useEffect(() => {
    setLoading(true)
    void run()
    const offEvent = window.guard.onEvent((event) => {
      if (!eventPrefix || eventPrefix.some((p) => event.startsWith(p))) void run()
    })
    const offState = window.guard.onState((s) => {
      if (s.connected) void run()
    })
    return () => {
      offEvent()
      offState()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run])

  return { data, error, loading, reload: run }
}
