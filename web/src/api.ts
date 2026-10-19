// 后端接口和数据类型

export interface Usage {
  input_tokens: number
  cached_tokens: number
  cache_write_tokens: number
  output_tokens: number
  cost?: number
}

export interface TurnUsage extends Usage {
  steps: number
  seconds: number
}

export type PreviewStatus = 'none' | 'idle' | 'starting' | 'running' | 'exited'

export interface PreviewInfo {
  status: PreviewStatus
  command?: string
  port?: number
  exit_code?: number | null
}

export interface Project {
  id: string
  name: string
  created_at: number
  updated_at: number
  profile_id?: string
  profile_name?: string
  usage: Usage
  cost: number
  running: boolean
  preview: PreviewInfo
}

export interface Profile {
  id: string
  name: string
  provider: 'openai' | 'anthropic'
  base_url: string
  model: string
  context_window: number
  max_output_tokens: number
  thinking: boolean
  vision: boolean
  effort: string
  price_input: number
  price_cached: number
  price_output: number
  api_key_set: boolean
  api_key_hint: string
}

export interface Preset {
  key: string
  label: string
  provider: 'openai' | 'anthropic'
  base_url: string
  models: string[]
  context_window: number
  max_output_tokens: number
  thinking: boolean
  vision: boolean
  docs: string
  note: string
  prices: Record<string, [number, number, number]>
}

export interface AgentSettings {
  max_steps: number
  autofix_rounds: number
  auto_check: boolean
  turn_budget_usd: number
  command_timeout: number
}

export interface Settings {
  profiles: Profile[]
  default_profile: string
  agent: AgentSettings
  presets: Preset[]
}

export interface Info {
  version: string
  sandbox: string
  sandbox_backend: string
  sandbox_status: string
  network: string
  preview_domain: string
  browser_check: boolean
  has_model: boolean
}

export interface Todo {
  content: string
  status: 'pending' | 'in_progress' | 'completed'
}

export interface Checkpoint {
  sha: string
  message: string
  files: FileChange[]
  additions: number
  deletions: number
}

export interface FileChange {
  path: string
  additions: number
  deletions: number
  status: 'added' | 'modified' | 'deleted' | 'renamed'
  binary: boolean
}

export interface ItemBase {
  id: string
  kind: string
  at: number
}

export interface UserItem extends ItemBase {
  kind: 'user'
  text: string
  images?: string[]
  queued?: boolean
  discarded?: boolean
  source?: 'autofix'
}

export interface TextItem extends ItemBase {
  kind: 'assistant' | 'thinking'
  text: string
  streaming?: boolean
  interrupted?: boolean
}

export interface ToolItem extends ItemBase {
  kind: 'tool'
  name: string
  title: string
  status: 'running' | 'ok' | 'error' | 'interrupted'
  output?: string
  args?: Record<string, unknown>
  detail?: Record<string, any>
  auto?: boolean
  ended?: number
}

export interface NoticeItem extends ItemBase {
  kind: 'notice'
  level: 'info' | 'warn' | 'error' | 'success'
  text: string
}

export interface TurnEndItem extends ItemBase {
  kind: 'turn_end'
  outcome: 'done' | 'stopped' | 'error' | 'limit' | 'refused'
  usage?: TurnUsage
  checkpoint?: Checkpoint | null
}

export type Item = UserItem | TextItem | ToolItem | NoticeItem | TurnEndItem

export interface Activity {
  kind: 'waiting' | 'preparing' | 'thinking' | 'writing' | 'tool_args' | 'tool' | 'compacting' | 'checking'
  name?: string
  title?: string
  chars?: number
}

export interface PreviewError {
  kind: string
  message: string
  url: string
  at: number
  count: number
}

export interface TimelineState {
  running?: boolean
  activity?: Activity | null
  turn_usage?: TurnUsage | null
  usage_total?: Usage
  todos?: Todo[] | null
  preview?: PreviewInfo
  preview_errors?: PreviewError[]
}

export interface Snapshot {
  items: Item[]
  state: TimelineState
  seq: number
  truncated: boolean
}

export type Op =
  | { op: 'add'; seq: number; item: Item }
  | { op: 'append'; seq: number; id: string; field: string; text: string }
  | { op: 'update'; seq: number; id: string; patch: Record<string, unknown> }
  | { op: 'state'; seq: number; patch: Partial<TimelineState> }

export interface FileEntry {
  path: string
  type: 'file' | 'dir'
  size?: number
}

export interface ProcessInfo {
  name: string
  command: string
  port: number | null
  running: boolean
  exit_code: number | null
  ready: boolean
  started_at: number
}

export interface ImageInput {
  media_type: string
  data: string
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let resp: Response
  try {
    resp = await fetch(path, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, '连不上服务，请确认 codeless 还在运行')
  }
  if (!resp.ok) {
    let message = `请求失败（HTTP ${resp.status}）`
    try {
      const data = await resp.json()
      if (typeof data.detail === 'string') message = data.detail
      else if (Array.isArray(data.detail)) message = data.detail.map((d: { msg: string }) => d.msg).join('；')
    } catch {
      /* 不是 JSON */
    }
    throw new ApiError(resp.status, message)
  }
  return resp.json() as Promise<T>
}

const p = (id: string) => `/api/projects/${id}`

export const api = {
  info: () => request<Info>('GET', '/api/info'),
  settings: () => request<Settings>('GET', '/api/settings'),
  saveProfile: (data: Partial<Profile> & { api_key?: string | null }) =>
    request<Profile>('POST', '/api/settings/profiles', data),
  deleteProfile: (id: string) => request('DELETE', `/api/settings/profiles/${id}`),
  setDefaultProfile: (id: string) => request('POST', `/api/settings/profiles/${id}/default`),
  testProfile: (data: Partial<Profile> & { api_key?: string }) =>
    request<{ ok: boolean; reply?: string; seconds?: number; error?: string }>('POST', '/api/settings/test', data),
  saveAgent: (data: Partial<AgentSettings>) => request<AgentSettings>('PUT', '/api/settings/agent', data),

  projects: () => request<Project[]>('GET', '/api/projects'),
  createProject: (data: { prompt?: string; images?: ImageInput[]; profile_id?: string; name?: string }) =>
    request<Project>('POST', '/api/projects', data),
  project: (id: string) => request<Project>('GET', p(id)),
  updateProject: (id: string, data: { name?: string; profile_id?: string }) =>
    request<Project>('PATCH', p(id), data),
  deleteProject: (id: string) => request('DELETE', p(id)),

  send: (id: string, text: string, images: ImageInput[]) =>
    request<{ queued: boolean }>('POST', `${p(id)}/messages`, { text, images }),
  stop: (id: string) => request('POST', `${p(id)}/stop`),
  fix: (id: string) => request('POST', `${p(id)}/fix`),
  clearErrors: (id: string) => request('POST', `${p(id)}/errors/clear`),

  files: (id: string) => request<{ files: FileEntry[] }>('GET', `${p(id)}/files`),
  file: (id: string, path: string) =>
    request<{ path: string; content: string | null; size: number; binary: boolean }>(
      'GET', `${p(id)}/file?path=${encodeURIComponent(path)}`),
  saveFile: (id: string, path: string, content: string) => request('PUT', `${p(id)}/file`, { path, content }),
  rawUrl: (id: string, path: string) => `${p(id)}/raw?path=${encodeURIComponent(path)}`,
  downloadUrl: (id: string) => `${p(id)}/download`,

  checkpoints: (id: string) =>
    request<{ sha: string; message: string; time: number }[]>('GET', `${p(id)}/checkpoints`),
  checkpoint: (id: string, sha: string) => request<{ sha: string; files: FileChange[] }>('GET', `${p(id)}/checkpoints/${sha}`),
  diff: (id: string, sha: string, path: string) =>
    request<{ diff: string }>('GET', `${p(id)}/diff?sha=${sha}&path=${encodeURIComponent(path)}`),
  restore: (id: string, sha: string) => request('POST', `${p(id)}/restore`, { sha }),

  processes: (id: string) => request<ProcessInfo[]>('GET', `${p(id)}/processes`),
  restartPreview: (id: string) => request('POST', `${p(id)}/preview/restart`),
  stopProcess: (id: string, name: string) => request('POST', `${p(id)}/processes/${name}/stop`),
}
