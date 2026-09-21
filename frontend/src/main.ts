import { ApiClient } from './api/client'
import { LiveClient } from './api/live'
import { mountApp } from './app/app'

const api = new ApiClient()
const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
const live = new LiveClient({ url: `${protocol}//${location.host}/api/v1/ws`, refresh: () => api.refresh() })
mountApp(document.getElementById('app') as HTMLElement, { api, live })
