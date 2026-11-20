/**
 * Connection to the MCP Guard daemon (backend/mcp_guard/api.py).
 *
 * Lives in the main process so the API token never reaches the renderer.
 * Keeps a live WebSocket for events (with reconnect) and wraps the REST API.
 */
import { EventEmitter } from 'node:events'
import { existsSync, readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import WebSocket from 'ws'
import type { GuardState, ToolRequest } from '../shared/types'

function guardHome(): string {
  return process.env.MCP_GUARD_HOME ?? join(homedir(), '.mcp-guard')
}

function readDaemonAddress(): { host: string; port: number } {
  const path = join(guardHome(), 'config.json')
  try {
    if (existsSync(path)) {
      const cfg = JSON.parse(readFileSync(path, 'utf8'))
      return { host: cfg.host ?? '127.0.0.1', port: cfg.port ?? 8765 }
    }
  } catch {
    /* fall through to defaults */
  }
  return { host: '127.0.0.1', port: 8765 }
}

function readToken(): string | null {
  if (process.env.MCP_GUARD_TOKEN) return process.env.MCP_GUARD_TOKEN
  const path = join(guardHome(), 'token')
  return existsSync(path) ? readFileSync(path, 'utf8').trim() : null
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message)
  }
}

type DaemonEvent = { event: string; data: any }

export class GuardClient extends EventEmitter {
  private ws: WebSocket | null = null
  private reconnectTimer: NodeJS.Timeout | null = null
  private retryDelay = 1000
  private stopped = false
  private pending = new Map<string, ToolRequest>()
  private connected = false
  private lastError: string | null = null
  private baseUrl = ''

  get state(): GuardState {
    const pending = [...this.pending.values()].sort((a, b) => a.created_at.localeCompare(b.created_at))
    return { connected: this.connected, daemonUrl: this.baseUrl, error: this.lastError, pending }
  }

  start(): void {
    this.stopped = false
    this.connect()
  }

  stop(): void {
    this.stopped = true
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.ws?.close()
  }

  /** Call the daemon's REST API. Settings and token are re-read so a daemon restart is picked up. */
  async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const { host, port } = readDaemonAddress()
    const token = readToken()
    if (!token) throw new ApiError(0, 'No MCP Guard token found. Start the daemon once: mcp-guard daemon')
    let resp: Response
    try {
      resp = await fetch(`http://${host}:${port}${path}`, {
        method,
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body)
      })
    } catch {
      throw new ApiError(0, `MCP Guard daemon is not reachable at ${host}:${port}`)
    }
    if (!resp.ok) {
      let detail = resp.statusText
      try {
        detail = (await resp.json()).detail ?? detail
      } catch {
        /* keep statusText */
      }
      throw new ApiError(resp.status, String(detail))
    }
    return (resp.status === 204 ? undefined : await resp.json()) as T
  }

  private connect(): void {
    const { host, port } = readDaemonAddress()
    this.baseUrl = `http://${host}:${port}`
    const token = readToken()
    if (!token) {
      this.setDisconnected('No token in ~/.mcp-guard/token — start the daemon once (mcp-guard daemon)')
      return this.scheduleReconnect()
    }

    const ws = new WebSocket(`ws://${host}:${port}/ws?token=${encodeURIComponent(token)}`)
    this.ws = ws

    ws.on('open', () => {
      this.retryDelay = 1000
      this.connected = true
      this.lastError = null
      this.emitState()
    })
    ws.on('message', (raw) => {
      try {
        this.handle(JSON.parse(raw.toString()) as DaemonEvent)
      } catch (err) {
        console.error('[guard] bad event', err)
      }
    })
    ws.on('close', (code) => {
      if (this.ws !== ws) return
      this.ws = null
      this.setDisconnected(code === 4401 ? 'Daemon rejected the token' : `Daemon not reachable at ${this.baseUrl}`)
      this.scheduleReconnect()
    })
    ws.on('error', () => {
      /* 'close' follows and handles reconnect */
    })
  }

  private handle(msg: DaemonEvent): void {
    switch (msg.event) {
      case 'snapshot':
        this.pending = new Map((msg.data.pending as ToolRequest[]).map((r) => [r.request_id, r]))
        this.emitState()
        break
      case 'request.created':
        this.pending.set(msg.data.request_id, msg.data)
        this.emit('created', msg.data as ToolRequest)
        this.emitState()
        break
      case 'request.decided':
        this.pending.delete(msg.data.request_id)
        this.emitState()
        break
    }
    this.emit('event', msg.event)
  }

  private setDisconnected(error: string): void {
    const changed = this.connected || this.lastError !== error || this.pending.size > 0
    this.connected = false
    this.lastError = error
    // Pending requests are re-sent in the snapshot after reconnecting.
    this.pending.clear()
    if (changed) this.emitState()
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.reconnectTimer) return
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, this.retryDelay)
    this.retryDelay = Math.min(this.retryDelay * 2, 10_000)
  }

  private emitState(): void {
    this.emit('state', this.state)
  }
}
