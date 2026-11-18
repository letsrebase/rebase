import type { Technology } from './techUsage'

/** One technology as the API reads it: a continuous Python, six months of six. */
export function technology(more: Partial<Technology> = {}): Technology {
  return {
    name: 'Python',
    band: 'continuous',
    sessions: 12,
    products: 3,
    months: 6,
    first_month: '2026-01',
    last_month: '2026-06',
    ...more,
  }
}
