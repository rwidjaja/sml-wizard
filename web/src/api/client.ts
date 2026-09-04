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

export interface SmlFile {
  name: string
  body: string
}

export interface GenerateSmlPayload {
  modelName: string
  catalogName?: string
  connectionName: string
  asConnection: string
  database: string
  schema: string
  dialect?: string | null
  nodes: unknown[]
  joins: unknown[]
  cfg: Record<string, unknown>
}

export interface DeploySteps {
  generate?: { fileCount: number }
  save?: { path: string }
  git?: { repoUrl: string; branch: string; commit: string; created: boolean }
  attach?: { repoId: string }
  deploy?: { projectId: string; projectName: string; reusedExistingProject: boolean }
}

export class DeployFailure extends Error {
  steps: DeploySteps
  constructor(message: string, steps: DeploySteps) {
    super(message)
    this.steps = steps
  }
}

/** The Deploy pipeline: generate SML -> save to disk -> create/push the
 *  model's GitHub repo -> attach that repo to AtScale -> compile + deploy. */
export async function deployModel(payload: GenerateSmlPayload) {
  const res = await fetch(`${BASE}/publish/deploy`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  const body = await res.json().catch(() => ({}))
  if (res.status === 422 && Array.isArray(body.errors)) {
    throw new SmlValidationFailure(body.errors)
  }
  if (!res.ok) {
    throw new DeployFailure(body?.error ?? `Deploy failed with ${res.status}`, body?.steps ?? {})
  }
  return body as { ok: boolean; steps: DeploySteps }
}

export class SmlValidationFailure extends Error {
  errors: string[]
  constructor(errors: string[]) {
    super(errors.join('; '))
    this.errors = errors
  }
}

export async function generateSml(payload: GenerateSmlPayload) {
  const res = await fetch(`${BASE}/sml/generate`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  const body = await res.json().catch(() => ({}))
  if (res.status === 422 && Array.isArray(body.errors)) {
    throw new SmlValidationFailure(body.errors)
  }
  if (!res.ok) {
    throw new Error(body?.error ?? `Generate failed with ${res.status}`)
  }
  return body as { files: SmlFile[] }
}

export function validateSml(files: SmlFile[]) {
  return request<{ passed: boolean; returncode: number; output: string }>('/sml/validate', {
    method: 'POST',
    body: JSON.stringify({ files }),
  })
}

export interface ImportedModel {
  nodes: unknown[]
  joins: unknown[]
  cfg: Record<string, unknown>
}

export function importSmlPath(path: string) {
  return request<ImportedModel>('/sml/import-path', { method: 'POST', body: JSON.stringify({ path }) })
}

export function importSmlGit(payload: { repoUrl?: string; branch?: string; connectionName?: string }) {
  return request<ImportedModel>('/sml/import-git', { method: 'POST', body: JSON.stringify(payload) })
}

/** Saving IS generating SML and writing it somewhere real - no separate
 *  proprietary state format. Loading is /sml/import-path or /sml/import-git. */
export function saveSmlToPath(path: string, files: SmlFile[]) {
  return request<{ ok: boolean; path: string; count: number }>('/sml/save-path', {
    method: 'POST',
    body: JSON.stringify({ path, files }),
  })
}
