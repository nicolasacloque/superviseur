export { editorSchema, MAX_SERIES, normalizeConfig, TrendConfigError } from './schema'
export type { TrendConfig, TrendSeriesConfig } from './schema'
export { render } from './trend'
export type { TrendDeps, TrendInstance } from './trend'

/** Type du widget dans le JSON d'un synoptique. */
export const type = 'trend'
