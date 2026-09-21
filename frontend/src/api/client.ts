import type {
  AdminApi,
  AdminUser,
  AuditEntry,
  AuditQuery,
  RoleInfo,
  Alarm,
  AlarmPage,
  AlarmQuery,
  AlarmsApi,
  HistoryApi,
  HistoryQuery,
  HistoryResult,
  PointInfo,
  PointsApi,
  PointTree,
  SessionUser,
  SynopticRecord,
  SynopticsApi,
  SynopticSummary,
  VersionInfo,
  WriteApi,
} from './types'
import type { SynopticDoc } from '../synoptic/model'

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

type Fetch = typeof fetch

/** Client REST : cookies de session (HttpOnly), un renouvellement automatique sur 401. */
export class ApiClient implements HistoryApi, AlarmsApi, PointsApi, WriteApi, SynopticsApi, AdminApi {
  constructor(
    private readonly base = '/api/v1',
    private readonly fetchImpl: Fetch = (input, init) => fetch(input, init),
  ) {}

  async login(login: string, password: string): Promise<void> {
    const response = await this.fetchImpl(`${this.base}/auth/login`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ login, password }),
    })
    if (!response.ok) throw new ApiError(response.status, await detail(response))
  }

  /** Renouvelle les jetons ; `false` si la session n'est plus valide (il faut se reconnecter). */
  async refresh(): Promise<boolean> {
    const response = await this.fetchImpl(`${this.base}/auth/refresh`, {
      method: 'POST',
      credentials: 'include',
    })
    return response.ok
  }

  async history(
    pointId: string,
    query: HistoryQuery,
    signal?: AbortSignal,
  ): Promise<HistoryResult> {
    const params = new URLSearchParams()
    if (query.from) params.set('from', query.from.toISOString())
    if (query.to) params.set('to', query.to.toISOString())
    if (query.bucket) params.set('bucket', query.bucket)
    if (query.maxPoints) params.set('max_points', String(query.maxPoints))
    return this.get<HistoryResult>(`/points/${pointId}/history?${params}`, signal)
  }

  async alarms(query: AlarmQuery, signal?: AbortSignal): Promise<AlarmPage> {
    const params = new URLSearchParams()
    if (query.state) params.set('state', query.state)
    if (query.path) params.set('path', query.path)
    if (query.limit) params.set('limit', String(query.limit))
    return this.get<AlarmPage>(`/alarms?${params}`, signal)
  }

  async acknowledge(alarmId: string): Promise<Alarm> {
    const request = () =>
      this.fetchImpl(`${this.base}/alarms/${alarmId}/ack`, { method: 'POST', credentials: 'include' })
    let response = await request()
    if (response.status === 401 && (await this.refresh())) response = await request()
    if (!response.ok) throw new ApiError(response.status, await detail(response))
    return ((await response.json()) as { alarm: Alarm }).alarm
  }

  async point(pointId: string): Promise<PointInfo> {
    return this.get<PointInfo>(`/points/${pointId}`)
  }

  async me(): Promise<SessionUser> {
    return this.get<SessionUser>('/auth/me')
  }

  async logout(): Promise<void> {
    await this.fetchImpl(`${this.base}/auth/logout`, { method: 'POST', credentials: 'include' })
  }

  async tree(path: string): Promise<PointTree> {
    return this.get<PointTree>(`/points/tree?${new URLSearchParams({ path })}`)
  }

  async search(query: string, limit = 50): Promise<PointInfo[]> {
    const params = new URLSearchParams({ q: query, limit: String(limit) })
    return (await this.get<{ items: PointInfo[] }>(`/points?${params}`)).items
  }

  async write(pointId: string, value: number | null, priority: number): Promise<void> {
    await this.send('POST', `/points/${pointId}/write`, { value, priority })
  }

  async synoptics(): Promise<SynopticSummary[]> {
    return this.get<SynopticSummary[]>('/synoptics')
  }

  async synoptic(slug: string): Promise<SynopticRecord> {
    return this.get<SynopticRecord>(`/synoptics/${encodeURIComponent(slug)}`)
  }

  async createSynoptic(doc: SynopticDoc, slug?: string): Promise<SynopticRecord> {
    return this.send<SynopticRecord>('POST', '/synoptics', slug ? { doc, slug } : { doc })
  }

  async saveSynoptic(id: string, doc: SynopticDoc, baseVersion?: number): Promise<SynopticRecord> {
    return this.send<SynopticRecord>('PUT', `/synoptics/${id}`, { doc, base_version: baseVersion })
  }

  async deleteSynoptic(id: string): Promise<void> {
    await this.send('DELETE', `/synoptics/${id}`)
  }

  async versions(slug: string): Promise<VersionInfo[]> {
    return this.get<VersionInfo[]>(`/synoptics/${encodeURIComponent(slug)}/versions`)
  }

  async version(slug: string, version: number): Promise<SynopticRecord> {
    return this.get<SynopticRecord>(`/synoptics/${encodeURIComponent(slug)}/versions/${version}`)
  }

  async restoreVersion(id: string, version: number): Promise<SynopticRecord> {
    return this.send<SynopticRecord>('POST', `/synoptics/${id}/restore/${version}`)
  }

  async users(): Promise<AdminUser[]> {
    return this.get<AdminUser[]>('/users')
  }

  async roles(): Promise<RoleInfo[]> {
    return this.get<RoleInfo[]>('/roles')
  }

  async createUser(login: string, password: string, role: string): Promise<AdminUser> {
    return this.send<AdminUser>('POST', '/users', { login, password, role })
  }

  async updateUser(id: string, patch: { role?: string; active?: boolean; password?: string }): Promise<AdminUser> {
    return this.send<AdminUser>('PATCH', `/users/${id}`, patch)
  }

  async unlockUser(id: string): Promise<AdminUser> {
    return this.send<AdminUser>('POST', `/users/${id}/unlock`)
  }

  async deleteUser(id: string): Promise<void> {
    await this.send('DELETE', `/users/${id}`)
  }

  async audit(query: AuditQuery = {}): Promise<AuditEntry[]> {
    const params = new URLSearchParams()
    if (query.action) params.set('action', query.action)
    if (query.limit) params.set('limit', String(query.limit))
    return this.get<AuditEntry[]>(`/audit?${params}`)
  }

  /** Requête avec corps JSON ; un 401 déclenche un renouvellement de session puis un second essai. */
  private async send<T = void>(method: string, path: string, body?: unknown): Promise<T> {
    const request = () =>
      this.fetchImpl(`${this.base}${path}`, {
        method,
        credentials: 'include',
        headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
    let response = await request()
    if (response.status === 401 && (await this.refresh())) response = await request()
    if (!response.ok) throw new ApiError(response.status, await detail(response))
    if (response.status === 204) return undefined as T
    return (await response.json()) as T
  }

  private async get<T>(path: string, signal?: AbortSignal): Promise<T> {
    const request = () =>
      this.fetchImpl(`${this.base}${path}`, { credentials: 'include', signal })
    let response = await request()
    if (response.status === 401 && (await this.refresh())) response = await request()
    if (!response.ok) throw new ApiError(response.status, await detail(response))
    return (await response.json()) as T
  }
}

async function detail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
    if (Array.isArray(body.detail)) return validationMessages(body.detail)
  } catch {
    // corps absent ou illisible : on garde le statut HTTP
  }
  return `HTTP ${response.status}`
}

/** Messages lisibles d'un 422 de validation (`[{loc, msg}]`). */
function validationMessages(errors: unknown[]): string {
  return errors
    .map((error) => {
      const { loc, msg } = error as { loc?: unknown[]; msg?: string }
      const where = (loc ?? []).filter((part) => part !== 'body').join(' > ')
      return `${where ? `${where} : ` : ''}${(msg ?? 'invalide').replace(/^Value error, /, '')}`
    })
    .join(' ; ')
}
