/** Routage par fragment d'URL (`#/view/<slug>`) : pas de configuration serveur nécessaire. */

export type Route =
  | { name: 'login' }
  | { name: 'list' }
  | { name: 'view'; slug: string }
  | { name: 'edit'; slug: string | null } // `null` : nouveau synoptique

export function parseRoute(hash: string): Route {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean)
  const decode = (part: string | undefined): string | null => {
    if (!part) return null
    try {
      return decodeURIComponent(part)
    } catch {
      return null
    }
  }
  const [head, arg] = parts
  if (head === 'login') return { name: 'login' }
  if (head === 'view') {
    const slug = decode(arg)
    if (slug) return { name: 'view', slug }
  }
  if (head === 'edit') {
    if (arg === 'new') return { name: 'edit', slug: null }
    const slug = decode(arg)
    if (slug) return { name: 'edit', slug }
  }
  return { name: 'list' }
}

export function hrefFor(route: Route): string {
  switch (route.name) {
    case 'login':
      return '#/login'
    case 'list':
      return '#/'
    case 'view':
      return `#/view/${encodeURIComponent(route.slug)}`
    case 'edit':
      return route.slug === null ? '#/edit/new' : `#/edit/${encodeURIComponent(route.slug)}`
  }
}
