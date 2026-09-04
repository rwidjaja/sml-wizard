const BASE = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(body?.error ?? `Request to ${path} failed with ${res.status}`)
  }
  return body as T
}

export interface LoginPayload {
  connectionName?: string
  url?: string
  username?: string
  password?: string
  apiToken?: string
  insecure?: boolean
}

export function login(payload: LoginPayload) {
  return request<{ ok: boolean; expiresAt: number }>('/session', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function checkSession() {
  return request<{ authenticated: boolean }>('/session')
}

export function fetchSavedConnections() {
  return request<{ names: string[] }>('/connections')
}

export interface SourceSummary {
  id: string
  label: string
  dialect: string | null
  connectionId: string
  database: string
}

export function fetchSources() {
  return request<SourceSummary[]>('/sources')
}

export interface SchemaColumn {
  name: string
  type: string
}

export interface SchemaTable {
  name: string
  columns: SchemaColumn[]
}

export interface SchemaEntry {
  name: string
  tables: SchemaTable[]
}

export function fetchSchemas(sourceId: string, search?: string) {
  const qs = search ? `?search=${encodeURIComponent(search)}` : ''
  return request<SchemaEntry[]>(`/sources/${encodeURIComponent(sourceId)}/schemas${qs}`)
}
