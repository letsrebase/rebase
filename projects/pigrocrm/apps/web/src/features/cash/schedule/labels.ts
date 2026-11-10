import { money } from '@/features/dashboard/format'
import { TAX_LABELS } from '@/features/taxes/labels'
import { formatIsoDayMonth } from '@/lib/dates'
import type { ModuleKey } from '@/lib/modules'
import type { StatusTone } from '@/components/StatusPill'
import type { DueItem, CashWeek } from './queries'

/**
 * The words of the schedule (the brief `2026-11-10-cash-and-schedule-brief.md`), written once.
 * Nothing here computes a figure: a sentence is composed from the server's strings, dates and
 * counts, and a «zero» is read off the string the API sent, never off a parsed number.
 */

export const LOADING_SENTENCE = 'Leggo le scadenze…'
export const NO_ACCOUNT_SENTENCE =
  'Nessun conto collegato. Aggiungi un conto per vedere il saldo di oggi e quello delle prossime settimane. Le scadenze delle fatture si vedono già.'
export const NO_ITEMS_SENTENCE = (weeks: number) =>
  `Niente da incassare né da pagare nelle prossime ${weeks} settimane.`
export const LATE_NOTE = 'Il saldo non conta ciò che è già scaduto'
export const NOT_IN_BALANCE = 'non tolte dal saldo'

/** A decimal string the API sent, read as zero without parsing it: `"0.00"`, `"-0.00"`. */
export function isZero(value: string): boolean {
  return /^-?0*(\.0*)?$/.test(value)
}

const formatters = new Map<string, Intl.NumberFormat>()

/** An amount in its own currency: euro as everywhere (`1.464,00 €`), another one with its code
 *  («1.350,00 USD»), the string going into the formatter verbatim. */
export function moneyIn(value: string, currency: string): string {
  if (currency === 'EUR') return money(value)
  try {
    let format = formatters.get(currency)
    if (!format) {
      format = new Intl.NumberFormat('it-IT', { style: 'currency', currency, useGrouping: 'always' })
      formatters.set(currency, format)
    }
    return format.format(value as `${number}`)
  } catch {
    return `${value.replace('.', ',')} ${currency}`
  }
}

/** «09/11 al 15/11»: a week as the server's `start` and `end` read. */
export function weekLabel(week: Pick<CashWeek, 'start' | 'end'>): string {
  return `${formatIsoDayMonth(week.start)} al ${formatIsoDayMonth(week.end)}`
}

/** The word of a count of items: «1 voce», «12 voci». */
export function itemsCount(count: number): string {
  return count === 1 ? '1 voce' : `${count} voci`
}

// -- the state of an item ----------------------------------------------------------------------

type ItemState = DueItem['state']

/** Tone of each state beside its words: expected is waiting (`gold`), forecast claims nothing
 *  yet (`muted`), settled is the ordinary ink, dropped is a thing that is gone (`muted`). */
export const ITEM_STATE_TONE: Record<ItemState, StatusTone> = {
  expected: 'gold',
  forecast: 'muted',
  settled: 'ink',
  dropped: 'muted',
}

const ITEM_STATE_LABEL: Record<ItemState, { in: string; out: string }> = {
  expected: { in: 'Da incassare', out: 'Da pagare' },
  forecast: { in: 'Previsto', out: 'Previsto' },
  settled: { in: 'Incassato', out: 'Pagato' },
  dropped: { in: 'Annullato', out: 'Annullato' },
}

/** What the pill of an open item says. A part-paid item names what arrived and what remains,
 *  both the server's figures (`settled`, `remaining`). */
export function itemPillLabel(item: DueItem): string {
  if (item.state === 'expected' && !isZero(item.settled)) {
    const done = item.direction === 'in' ? 'Incassati' : 'Pagati'
    return `${done} ${moneyIn(item.settled, item.currency)}, restano ${moneyIn(item.remaining, item.currency)}`
  }
  return ITEM_STATE_LABEL[item.state][item.direction]
}

/** The pill of a late row: «Scaduto da 12 giorni», the days being the server's. */
export function lateLabel(days: number): string {
  return days === 1 ? 'Scaduto da 1 giorno' : `Scaduto da ${days} giorni`
}

/** The sentence under a date that follows another item (design § 1.3). */
export function estimatedSentence(direction: DueItem['direction']): string {
  return direction === 'in' ? 'in attesa dell’incasso' : 'in attesa del pagamento'
}

// -- the source of an item ---------------------------------------------------------------------

type Page = 'invoice' | 'purchase'

interface SourceInfo {
  label: string
  /** The modules that must all be on for the kind to be drawn (a kind of a module that is off
   *  or not built is absent from the schedule). */
  modules: readonly ModuleKey[]
  /** Where the source has a page. */
  page?: Page
}

/** Every kind the server may send, with the module that owns it. A kind that is not here is
 *  not drawn. */
export const SOURCES: Record<string, SourceInfo> = {
  sales_invoice: { label: 'Fattura emessa', modules: [], page: 'invoice' },
  sales_draft: { label: 'Bozza di fattura', modules: [] },
  purchase: { label: 'Acquisto', modules: ['purchases'], page: 'purchase' },
  recurring_expense: { label: 'Spesa ricorrente', modules: ['purchases'] },
  contract_occurrence: { label: 'Contratto', modules: ['contracts'] },
  integration_vat: { label: 'Integrazione IVA', modules: ['taxes'] },
  iva_settlement: { label: 'Liquidazione IVA', modules: ['taxes'] },
  withholding: { label: 'Ritenute', modules: ['taxes'] },
  tax_instalment: { label: 'Imposte', modules: ['taxes'] },
  cash_basis_vat: { label: 'IVA per cassa', modules: ['taxes'] },
  payroll: { label: 'Stipendi', modules: ['staff'] },
  engagement: { label: 'Collaboratore', modules: ['collaborators'] },
}

/** Kinds that only an administrator reads and settles (`RESTRICTED_SOURCE_KINDS` in the core):
 *  the permission of «Segna come pagato» follows them as it follows money going out. */
const RESTRICTED_KINDS: Record<string, true> = {
  integration_vat: true,
  iva_settlement: true,
  withholding: true,
  tax_instalment: true,
  payroll: true,
  cash_basis_vat: true,
  engagement: true,
}

/** Whether settling `item` is an administrator's act (design § 5, `lib/permissions.ts`). */
export function settlesAsAdmin(item: Pick<DueItem, 'direction' | 'source_kind'>): boolean {
  return item.direction === 'out' || RESTRICTED_KINDS[item.source_kind] === true
}

export function sourceLabel(kind: string): string {
  return SOURCES[kind]?.label ?? kind
}

export function sourcePage(item: Pick<DueItem, 'source_kind' | 'source_id'>): Page | null {
  return item.source_id ? (SOURCES[item.source_kind]?.page ?? null) : null
}

/** Who the item is with: the organisation, the tax by its name, or the staff. */
export function counterpartyName(item: DueItem): string {
  if (item.organization_name) return item.organization_name
  if (item.counterparty_type === 'tax_office') return taxName(item.tax) ?? 'Erario'
  if (item.counterparty_type === 'staff_member') return 'Personale'
  return sourceLabel(item.source_kind)
}

/** What the item is: its description, or the kind of document when it has none. */
export function itemWhat(item: DueItem): string {
  return item.description ?? sourceLabel(item.source_kind)
}

/** A tax by the name the «Tasse» page uses; a code it does not know is shown as it came. */
export function taxName(tax: string | null | undefined): string | null {
  if (!tax) return null
  return (TAX_LABELS as Record<string, string>)[tax] ?? tax
}
