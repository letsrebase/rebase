import type { StatusTone } from '@/components/StatusPill'
import { formatMoneyValue } from '@/features/time/columns'
import { formatIsoDateItalian } from '@/lib/dates'
import { KIND_LABELS } from './formValues'
import type { Purchase, PurchaseStatus } from './queries'

/** The words of a purchase's state; «Registrata» is the ordinary one and the list does not draw it. */
export const PURCHASE_STATUS_LABELS: Record<PurchaseStatus, string> = {
  registered: 'Registrata',
  cancelled: 'Annullato',
}

/** Next to the labels, so a state added to the API makes this fail to compile. */
export const PURCHASE_STATUS_TONE: Record<PurchaseStatus, StatusTone> = {
  registered: 'ink',
  cancelled: 'muted',
}

/** «Fattura 2026/0418»: the kind and, when the document has one, its number. */
export function documentLabel(row: Pick<Purchase, 'kind' | 'number'>): string {
  const kind = KIND_LABELS[row.kind]
  return row.number === null || row.number === '' ? kind : `${kind} ${row.number}`
}

/** «Fattura 14/E · 28/10/2026»: the document line of a phone card. */
export function documentLine(row: Pick<Purchase, 'kind' | 'number' | 'date'>): string {
  return `${documentLabel(row)} · ${formatIsoDateItalian(row.date)}`
}

/** What is special about a purchase, as the chips of its first cell, in the order they read. */
export function specialChips(
  row: Pick<Purchase, 'kind' | 'reverse_charge' | 'split_payment'>,
): string[] {
  const chips: string[] = []
  if (row.kind === 'credit_note') chips.push('Nota di credito')
  if (row.reverse_charge) chips.push('Inversione contabile')
  if (row.split_payment) chips.push('Scissione dei pagamenti')
  return chips
}

/** «USD 540,00» under the euro amount of a purchase paid in another currency; the original
 *  amount is the one the API stored, never recomputed from a rate. */
export function foreignAmount(
  row: Pick<Purchase, 'currency' | 'currency_amount'>,
): string | null {
  if (row.currency === 'EUR' || row.currency_amount === null) return null
  return `${row.currency} ${formatMoneyValue(row.currency_amount).replace(/\s*€$/, '')}`
}

/** «ritenuta 270,00 €»: only what the API stored. The net to pay is the server's to say. */
export function withholdingLabel(row: Pick<Purchase, 'withholding_amount'>): string | null {
  return row.withholding_amount === null
    ? null
    : `ritenuta ${formatMoneyValue(row.withholding_amount)}`
}
