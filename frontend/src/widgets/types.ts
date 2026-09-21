/** Contrat commun des widgets de synoptique (section 10.3 du cahier des charges). */

export type EditorField =
  | { key: string; label: string; type: 'points'; min: number; max: number }
  | { key: string; label: string; type: 'select'; options: { value: string; label: string }[]; default: string }
  | { key: string; label: string; type: 'boolean'; default: boolean }
  | { key: string; label: string; type: 'number'; optional: true }

export interface WidgetInstance {
  /** Reçoit une nouvelle valeur temps réel. */
  update(sample: import('../api/types').LiveSample): void
  destroy(): void
}
