/** Application du style des widgets et des règles d'affichage. */

import { compile, type Compiled, type ExprVars } from './expressions'
import type { Rule, StyleMap } from './model'

/** Miroir de la validation serveur : rien qui déclencherait une requête ou sortirait de la déclaration. */
export function isSafeCssValue(value: unknown): boolean {
  if (typeof value === 'number') return Number.isFinite(value)
  if (typeof value !== 'string' || value.length > 100) return false
  const lowered = value.toLowerCase()
  if (['url(', 'expression', '@import', 'javascript:'].some((token) => lowered.includes(token))) return false
  return !/[;{}<>\\]/.test(value)
}

const PX_KEYS = new Set(['fontSize', 'strokeWidth', 'borderRadius'])
/** Propriétés appliquées directement à l'élément ; toutes sont aussi exposées en variables CSS. */
const DIRECT_KEYS = new Set(['color', 'background', 'opacity', 'fontSize', 'fontWeight', 'textAlign', 'borderColor', 'borderRadius'])

const kebab = (key: string) => key.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)

export interface CompiledRule {
  test(vars: ExprVars): boolean
  style: StyleMap
}

/** Compile les règles une fois ; une expression invalide est ignorée (elle ne casse pas le widget). */
export function compileRules(rules: Rule[] | undefined): CompiledRule[] {
  const compiled: CompiledRule[] = []
  for (const rule of rules ?? []) {
    try {
      const expression: Compiled = compile(rule.when)
      compiled.push({ test: (vars) => expression.test(vars), style: rule.style ?? {} })
    } catch {
      // règle invalide : l'éditeur la signale, le viewer l'ignore
    }
  }
  return compiled
}

/** Style de base puis règles dans l'ordre : la dernière règle vraie l'emporte sur les précédentes. */
export function resolveStyle(base: StyleMap, rules: CompiledRule[], vars: ExprVars): StyleMap {
  const style: StyleMap = { ...base }
  for (const rule of rules) if (rule.test(vars)) Object.assign(style, rule.style)
  return style
}

/** Applique `style` à `element`, en retirant ce que le style précédent posait et que celui-ci n'a plus. */
export function applyStyle(element: HTMLElement, style: StyleMap, previous: StyleMap = {}): void {
  for (const key of Object.keys(previous)) {
    if (key in style) continue
    element.style.removeProperty(`--w-${kebab(key)}`)
    if (DIRECT_KEYS.has(key)) element.style.removeProperty(kebab(key))
  }
  for (const [key, raw] of Object.entries(style)) {
    if (!isSafeCssValue(raw)) continue
    const value = typeof raw === 'number' && PX_KEYS.has(key) ? `${raw}px` : String(raw)
    element.style.setProperty(`--w-${kebab(key)}`, value)
    if (DIRECT_KEYS.has(key)) element.style.setProperty(kebab(key), value)
  }
}
