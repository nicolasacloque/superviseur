import type {
  Alarm,
  AlarmPage,
  AlarmQuery,
  AlarmsApi,
  HistoryApi,
  HistoryQuery,
  HistoryResult,
  PointInfo,
} from './types'

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
export class ApiClient implements HistoryApi, AlarmsApi {
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
  } catch {
    // corps absent ou illisible : on garde le statut HTTP
  }
  return `HTTP ${response.status}`
}
