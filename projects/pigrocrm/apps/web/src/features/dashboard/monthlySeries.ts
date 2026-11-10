/** The four cash series of the economic charts, in stacking order, with the tile
 *  palette each one wears (see MonthlyBars.tsx for why the brand palette and not a
 *  validated categorical set). */
export type SeriesKey = 'paid' | 'unpaid' | 'drafts' | 'costs'

export const SERIES: Record<SeriesKey, { label: string; color: string }> = {
  paid: { label: 'Ricavi incassati', color: 'var(--color-prussian-blue)' },
  unpaid: { label: 'Da incassare', color: 'var(--color-royal-gold)' },
  drafts: { label: 'Bozze / proforma', color: 'var(--color-charcoal-blue)' },
  costs: { label: 'Costi passivi', color: 'var(--color-watermelon-strong)' },
}

