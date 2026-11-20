// Mirrors the Python models in backend/mcp_guard/models.py.

export type RequestStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'EXPIRED' | 'CANCELLED'
export type PolicyAction = 'allow' | 'block' | 'ask'

export interface ToolRequest {
  request_id: string
  server: string
  tool: string
  arguments: Record<string, unknown>
  status: RequestStatus
  decided_by: 'user' | 'policy' | 'timeout' | 'client' | 'system' | null
  decision_reason: string | null
  created_at: string
  decided_at: string | null
  expires_at: string | null
  execution_status: 'success' | 'error' | null
  result_preview: string | null
  error: string | null
  duration_ms: number | null
  completed_at: string | null
}

export interface PolicyRule {
  id: number
  server: string
  tool: string
  action: PolicyAction
  note: string | null
  created_at: string
}

export interface GuardSettings {
  host: string
  port: number
  approval_timeout_seconds: number
  default_action: PolicyAction
}

export type Stats = Record<RequestStatus | 'TOTAL', number>

/** Live state pushed from the main process to every window. */
export interface GuardState {
  connected: boolean
  daemonUrl: string
  error: string | null
  pending: ToolRequest[]
}

export interface ListFilter {
  status?: RequestStatus
  server?: string
  limit?: number
}

export interface DecisionInput {
  requestId: string
  decision: 'allow' | 'block'
  remember: boolean
}

export interface RuleInput {
  server: string
  tool: string
  action: PolicyAction
  note?: string
}

/** The API exposed to the renderer via contextBridge as `window.guard`. */
export interface GuardApi {
  getState(): Promise<GuardState>
  onState(cb: (state: GuardState) => void): () => void
  /** Fires for every daemon event (request.created, request.decided, rule.created, ...). */
  onEvent(cb: (event: string) => void): () => void
  decide(input: DecisionInput): Promise<ToolRequest>
  listRequests(filter: ListFilter): Promise<ToolRequest[]>
  stats(): Promise<Stats>
  listRules(): Promise<PolicyRule[]>
  addRule(rule: RuleInput): Promise<PolicyRule>
  deleteRule(id: number): Promise<void>
  getSettings(): Promise<GuardSettings>
  updateSettings(patch: Partial<Pick<GuardSettings, 'approval_timeout_seconds' | 'default_action'>>): Promise<GuardSettings>
  openMainWindow(): void
}
