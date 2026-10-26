import type { StatusTone } from '@/components/StatusPill'
import type { components } from '@/lib/api-types'

/**
 * What the invoices page's «Importa» knows about a review, and none of how it is drawn:
 * the outcomes the API names, their words, which rows a confirmation would write, and how
 * a year of files is cut into requests. Pure, so the rules a person relies on («Importa 12
 * fatture» means twelve, a customer I unchecked is not created) are tested without a
 * dialog around them.
 */

export type ReviewRow = components['schemas']['UploadedInvoiceReviewRead']
export type ConfirmRow = components['schemas']['UploadedInvoiceConfirmRead']
export type ParsedInvoice = components['schemas']['ParsedInvoice']
export type ReviewOutcome = ReviewRow['outcome']
export type ConfirmOutcome = ConfirmRow['outcome']

/** What the confirmation takes: one decision per new customer, keyed by the `party_key`
 *  the review named (`UploadedInvoiceDecisions`). */
export interface ImportDecisions {
  clienti: Record<string, { create_customer?: true; customer_id?: string }>
}

/** The words of each verdict. Typed on the API's own union, so a seventh outcome added
 *  there fails to compile here instead of rendering as nothing. */
export const REVIEW_OUTCOME_LABELS: Record<ReviewOutcome, string> = {
  ready: 'Da importare',
  needs_customer_confirmation: 'Cliente nuovo',
  already_present: 'Già presente',
  conflict: 'In conflitto',
  incoming_skipped: 'Ricevuta',
  unclaimed: 'Non riconosciuto',
}

export const REVIEW_OUTCOME_TONES: Record<ReviewOutcome, StatusTone> = {
  ready: 'accent',
  needs_customer_confirmation: 'gold',
  already_present: 'muted',
  conflict: 'danger',
  incoming_skipped: 'muted',
  unclaimed: 'danger',
}

/** The sentence under a verdict that is not obvious from its word. `ready` and the
 *  customer one say nothing: the row's own columns are what will be created. */
export const REVIEW_OUTCOME_NOTES: Record<ReviewOutcome, string> = {
  ready: '',
  needs_customer_confirmation: '',
  already_present: 'Lo stesso file è già nel registro: non viene importato di nuovo.',
  conflict: 'Questo numero è già nel registro con un file diverso.',
  incoming_skipped:
    'Il fornitore non è una delle tue aziende: è una fattura che hai ricevuto, non va nel registro delle emesse.',
  unclaimed: 'Non è una fattura FatturaPA FPR12.',
}

export const CONFIRM_OUTCOME_LABELS: Record<ConfirmOutcome, string> = {
  imported: 'Importate',
  already_present: 'Già presenti',
  conflict: 'Rifiutate dal registro',
  incoming_skipped: 'Ricevute, non importate',
  needs_customer_confirmation: 'Senza cliente, non importate',
  unclaimed: 'Non riconosciute',
}

/** Problems first, the rows that need a look before a press, then what will simply
 *  happen: a year of «Da importare» must not bury the one file that does not read. */
const REVIEW_ORDER: ReviewOutcome[] = [
  'conflict',
  'unclaimed',
  'needs_customer_confirmation',
  'incoming_skipped',
  'already_present',
  'ready',
]

export function sortReviewRows(rows: readonly ReviewRow[]): ReviewRow[] {
  return rows
    .map((row, index) => ({ row, index }))
    .sort(
      (a, b) =>
        REVIEW_ORDER.indexOf(a.row.outcome) - REVIEW_ORDER.indexOf(b.row.outcome) ||
        a.index - b.index,
    )
    .map(({ row }) => row)
}

export function countByOutcome(rows: readonly ReviewRow[]): Partial<Record<ReviewOutcome, number>> {
  const counts: Partial<Record<ReviewOutcome, number>> = {}
  for (const row of rows) counts[row.outcome] = (counts[row.outcome] ?? 0) + 1
  return counts
}

/** A counterparty the register has never seen, as the files state it. One decision per
 *  party, not per file: a year of invoices to one new customer is one checkbox. */
export interface NewParty {
  key: string
  name: string
  taxId: string
  invoices: number
}

export function newParties(rows: readonly ReviewRow[]): NewParty[] {
  const found = new Map<string, NewParty>()
  for (const row of rows) {
    if (row.outcome !== 'needs_customer_confirmation' || !row.invoice || !row.party_key) continue
    const party = found.get(row.party_key)
    if (party) party.invoices += 1
    else {
      const { cliente } = row.invoice
      found.set(row.party_key, {
        key: row.party_key,
        name: cliente.ragione_sociale,
        taxId: cliente.partita_iva ?? cliente.codice_fiscale ?? '',
        invoices: 1,
      })
    }
  }
  return [...found.values()]
}

/** Whether confirming would write this row: a verdict of `ready`, or a new customer the
 *  person has left ticked. Nothing else is ever written, whatever else is in the batch. */
export function willBeWritten(row: ReviewRow, declined: ReadonlySet<string>): boolean {
  if (row.outcome === 'ready') return true
  return (
    row.outcome === 'needs_customer_confirmation' &&
    row.party_key != null &&
    !declined.has(row.party_key)
  )
}

export function countWrites(rows: readonly ReviewRow[], declined: ReadonlySet<string>): number {
  return rows.filter((row) => willBeWritten(row, declined)).length
}

/** The decisions to send, one per new customer left ticked. A customer an earlier batch
 *  already created is attached by id instead of created again, which is what keeps a
 *  counterparty the register cannot match by tax id (a foreign one) at one customer
 *  however many batches its invoices fall in. An unticked customer gets none, and the
 *  server then answers its invoices `needs_customer_confirmation` and writes nothing,
 *  which is what unticking means. */
export function decisionsFor(
  rows: readonly ReviewRow[],
  declined: ReadonlySet<string>,
  created: ReadonlyMap<string, string> = new Map(),
): ImportDecisions {
  const clienti: ImportDecisions['clienti'] = {}
  for (const row of rows) {
    if (row.outcome !== 'needs_customer_confirmation' || !willBeWritten(row, declined)) continue
    const key = row.party_key as string
    const customerId = created.get(key)
    clienti[key] = customerId === undefined ? { create_customer: true } : { customer_id: customerId }
  }
  return { clienti }
}

/** The customer each party's imported invoices went to, from a confirmation's rows. */
export function customersOf(rows: readonly ConfirmRow[]): Map<string, string> {
  const customers = new Map<string, string>()
  for (const row of rows) {
    if (row.outcome === 'imported' && row.party_key && row.fattura) {
      customers.set(row.party_key, row.fattura.customer_id)
    }
  }
  return customers
}

/** One request's worth of files. The review and the write both go in these, one after
 *  the other: a request is read and written invoice by invoice on the server, and a
 *  year in one body would sit behind a single spinner with no way to say how far along
 *  it is. A ZIP is never split, so a batch may exceed `maxBytes` by exactly one. */
export const BATCH_MAX_FILES = 25
export const BATCH_MAX_BYTES = 8 * 1024 * 1024

export function batchFiles(
  files: readonly File[],
  maxFiles = BATCH_MAX_FILES,
  maxBytes = BATCH_MAX_BYTES,
): File[][] {
  const batches: File[][] = []
  let current: File[] = []
  let bytes = 0
  for (const file of files) {
    if (current.length > 0 && (current.length >= maxFiles || bytes + file.size > maxBytes)) {
      batches.push(current)
      current = []
      bytes = 0
    }
    current.push(file)
    bytes += file.size
  }
  if (current.length > 0) batches.push(current)
  return batches
}

/** The numbers a register year still lacks after the import, as the last invoice written
 *  into it reports them (`buchi_non_dichiarati` is read after each write, so the last one
 *  of a year is the register's state when the import ended). */
export interface RegisterYear {
  aziendaId: string | null
  anno: number
  missing: number[]
}

export function registerYears(rows: readonly ConfirmRow[]): RegisterYear[] {
  const years = new Map<string, RegisterYear>()
  for (const row of rows) {
    if (row.outcome !== 'imported' || !row.fattura || row.buchi_non_dichiarati == null) continue
    const { anno } = row.fattura
    if (anno == null) continue
    years.set(`${row.azienda_id ?? ''}/${anno}`, {
      aziendaId: row.azienda_id ?? null,
      anno,
      missing: row.buchi_non_dichiarati,
    })
  }
  return [...years.values()].sort((a, b) => a.anno - b.anno)
}

export function countConfirmed(rows: readonly ConfirmRow[]): Partial<Record<ConfirmOutcome, number>> {
  const counts: Partial<Record<ConfirmOutcome, number>> = {}
  for (const row of rows) counts[row.outcome] = (counts[row.outcome] ?? 0) + 1
  return counts
}
