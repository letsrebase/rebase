/**
 * Words and shapes for the figures of the IVA page. Nothing here is arithmetic: a rate, a share and
 * an amount are the strings the server sent, and what these functions do is trim and place them.
 */

/** `"22.00"` as «22 %», `"5.50"` as «5,5 %»: the trailing zeros of a percentage dropped by
 *  splitting the string, never by parsing it. */
export function formatPercent(value: string): string {
  const [whole = '', fraction = ''] = value.split('.')
  const trimmed = fraction.replace(/0+$/, '')
  return `${trimmed === '' ? whole : `${whole},${trimmed}`} %`
}

/** A group's rate («22 %»), or its nature («N2.1») when the rate is zero and the nature says why. */
export function rateLabel(rate: string, nature: string | null): string {
  return nature !== null ? nature : formatPercent(rate)
}

/** The rate column of a document: its one group's, or «2 aliquote» for a mixed one. */
export function groupsLabel(groups: ReadonlyArray<{ rate: string; nature: string | null }>): string {
  const [first] = groups
  if (first === undefined) return '—'
  return groups.length === 1 ? rateLabel(first.rate, first.nature) : `${groups.length} aliquote`
}
