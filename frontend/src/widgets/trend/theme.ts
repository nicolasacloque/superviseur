/**
 * Couleurs du graphique : palette catégorielle de référence, validée en clair et en sombre
 * (écart CVD >= 8 entre séries voisines, contraste >= 3:1 sur la surface sombre). L'ordre des
 * huit teintes est fixe : la couleur suit la position dans la configuration, jamais un cycle.
 */

export interface Theme {
  mode: 'light' | 'dark'
  surface: string
  ink: string
  inkSecondary: string
  muted: string
  grid: string
  axis: string
  series: string[]
}

export const LIGHT: Theme = {
  mode: 'light',
  surface: '#fcfcfb',
  ink: '#0b0b0b',
  inkSecondary: '#52514e',
  muted: '#898781',
  grid: '#e1e0d9',
  axis: '#c3c2b7',
  series: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948'],
}

export const DARK: Theme = {
  mode: 'dark',
  surface: '#1a1a19',
  ink: '#ffffff',
  inkSecondary: '#c3c2b7',
  muted: '#898781',
  grid: '#2c2c2a',
  axis: '#383835',
  series: ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767'],
}

export type ThemePreference = 'auto' | 'light' | 'dark'

/** `auto` suit le thème stampé sur la page (`data-theme`), sinon le réglage du système. */
export function resolveTheme(preference: ThemePreference, prefersDark: boolean, stamped?: string): Theme {
  if (preference === 'light') return LIGHT
  if (preference === 'dark') return DARK
  if (stamped === 'dark') return DARK
  if (stamped === 'light') return LIGHT
  return prefersDark ? DARK : LIGHT
}
