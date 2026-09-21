export { editorSchema, AlarmListConfigError, normalizeConfig } from './schema'
export type { AlarmListConfig } from './schema'
export { render } from './alarm_list'
export type { AlarmListDeps, AlarmListInstance } from './alarm_list'

/** Type du widget dans le JSON d'un synoptique. */
export const type = 'alarm_list'
