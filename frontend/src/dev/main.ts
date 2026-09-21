import { ApiClient } from '../api/client'
import { LiveClient } from '../api/live'
import type { HistoryApi, LiveApi } from '../api/types'
import { render } from '../widgets/trend'
import { MOCK_POINTS, MockApi, MockLive } from './mock'

/**
 * Page de démonstration du widget `trend`.
 * - par défaut : données simulées ;
 * - `?api=1&points=<uuid>,<uuid>` : API réelle (proxy Vite vers 127.0.0.1:8000), après connexion.
 */
const params = new URLSearchParams(location.search)
const useApi = params.get('api') === '1'

let api: HistoryApi
let live: LiveApi
const mockLive = useApi ? null : new MockLive()
if (useApi) {
  const client = new ApiClient()
  api = client
  live = new LiveClient({ url: '/api/v1/ws', refresh: () => client.refresh() })
} else {
  api = new MockApi()
  live = mockLive as MockLive
}
document.getElementById('mode')!.textContent = useApi ? 'API réelle' : 'données simulées'

// Le thème est posé avant le rendu : le widget résout ses couleurs à la création.
const stored = params.get('theme')
if (stored === 'light' || stored === 'dark') {
  document.documentElement.dataset.theme = stored
  ;(document.getElementById('theme') as HTMLSelectElement).value = stored
}

const grid = document.getElementById('widgets')!
function cell(wide = false): HTMLElement {
  const node = document.createElement('div')
  node.className = wide ? 'cell cell--wide' : 'cell'
  grid.append(node)
  return node
}

const ids = useApi ? (params.get('points') ?? '').split(',').filter(Boolean) : []
const demos = useApi
  ? [{ points: ids.slice(0, 8).map((point) => ({ point })), range: '1h', wide: true }]
  : [
      { points: [{ point: 'soufflage' }, { point: 'reprise' }, { point: 'exterieur' }], range: '1h', wide: false },
      { points: [{ point: 'puissance', label: 'Puissance' }], range: '24h', wide: false },
      {
        points: Object.keys(MOCK_POINTS)
          .filter((id) => id.startsWith('zone'))
          .map((point) => ({ point })),
        range: '6h',
        wide: true,
      },
    ]

for (const demo of demos) {
  if (demo.points.length === 0) continue
  render(cell(demo.wide), { bind: { points: demo.points }, range: demo.range }, { api, live })
}

document.getElementById('theme')!.addEventListener('change', (event) => {
  const value = (event.target as HTMLSelectElement).value
  if (value === 'auto') delete document.documentElement.dataset.theme
  else document.documentElement.dataset.theme = value
  const next = new URL(location.href)
  if (value === 'auto') next.searchParams.delete('theme')
  else next.searchParams.set('theme', value)
  location.href = next.toString() // les couleurs du tracé sont résolues au rendu
})

const cut = document.getElementById('cut') as HTMLButtonElement
cut.addEventListener('click', () => {
  if (!mockLive) return
  const closing = mockLive.state === 'open'
  mockLive.setState(closing ? 'closed' : 'open')
  cut.textContent = closing ? 'Rétablir le direct' : 'Couper le direct'
})
if (useApi) cut.hidden = true
