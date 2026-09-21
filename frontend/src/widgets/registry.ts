import type { WidgetType } from '../synoptic/model'
import { alarmListModule } from './alarm_list/module'
import type { WidgetModule } from './base'
import { gaugeModule } from './gauge/gauge'
import { imageModule } from './image/image'
import { indicatorModule } from './indicator/indicator'
import { labelModule } from './label/label'
import { linkModule } from './link/link'
import { setpointModule } from './setpoint/setpoint'
import { shapeModule } from './shape/shape'
import { switchModule } from './switch/switch'
import { trendModule } from './trend/module'
import { valueModule } from './value/value'

/** Les widgets de la section 10.3, par type. */
export const MODULES: Record<WidgetType, WidgetModule> = {
  value: valueModule,
  label: labelModule,
  gauge: gaugeModule,
  indicator: indicatorModule,
  switch: switchModule,
  setpoint: setpointModule,
  trend: trendModule,
  alarm_list: alarmListModule,
  image: imageModule,
  link: linkModule,
  shape: shapeModule,
}

export function moduleFor(type: string): WidgetModule | undefined {
  return (MODULES as Record<string, WidgetModule | undefined>)[type]
}
