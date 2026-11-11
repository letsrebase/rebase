import { daysCount } from '@/features/cash/days'
import { moneyIn } from '@/features/cash/money'
import { TAX_LABELS } from '@/features/taxes/labels'
import { formatIsoDayMonth } from '@/lib/dates'
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
/** A solo space has today's balance only: it has no projected balance to promise. */
export const NO_ACCOUNT_SOLO_SENTENCE =
  'Nessun conto collegato. Aggiungi un conto per vedere il saldo di oggi. Le scadenze delle fatture si vedono già.'
export const NO_ITEMS_SENTENCE = (weeks: number) =>
  `Niente da incassare né da pagare nelle prossime ${weeks} settimane.`
export const LATE_NOTE = 'Il saldo non conta ciò che è già scaduto'
export const NOT_IN_BALANCE = 'non tolte dal saldo'

/** A decimal string the API sent, read as zero without parsing it: `"0.00"`, `"-0.00"`. */
export function isZero(value: string): boolean {
  return /^-?0*(\.0*)?$/.test(value)
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
  return `Scaduto da ${daysCount(days)}`
}

/** The tone of the rows that are not an item's own state: a late row is `danger`. */
export const ROW_TONE = { overdue: 'danger' } as const satisfies Record<string, StatusTone>

/** The sentence under a date that follows another item (design § 1.3). */
export function estimatedSentence(direction: DueItem['direction']): string {
  return direction === 'in' ? 'in attesa dell’incasso' : 'in attesa del pagamento'
}

// -- the source of an item ---------------------------------------------------------------------

type Page = 'invoice' | 'purchase'

interface SourceInfo {
  label: string
  /** Where the source has a page. */
  page?: Page
}

/** The kinds this build names. Which of them a viewer sees is the server's call (the module that
 *  owns a kind, the role): a row of a kind that is not here is drawn all the same. */
export const SOURCES: Record<string, SourceInfo> = {
  sales_invoice: { label: 'Fattura emessa', page: 'invoice' },
  sales_draft: { label: 'Bozza di fattura' },
  purchase: { label: 'Acquisto', page: 'purchase' },
  recurring_expense: { label: 'Spesa ricorrente' },
  contract_occurrence: { label: 'Contratto' },
  integration_vat: { label: 'Integrazione IVA' },
  iva_settlement: { label: 'Liquidazione IVA' },
  withholding: { label: 'Ritenute' },
  tax_instalment: { label: 'Rata imposte' },
  notice_plan: { label: 'Rata di un avviso' },
  tax_balance: { label: 'Saldo imposte' },
  tax_advance: { label: 'Acconto imposte' },
  chamber_fee: { label: 'Diritto camerale' },
  tax_credit: { label: "Credito d'imposta" },
  cash_basis_vat: { label: 'IVA per cassa' },
  payroll: { label: 'Stipendi' },
  engagement: { label: 'Collaboratore' },
}

/** Kinds that only an administrator reads and settles (`RESTRICTED_SOURCE_KINDS` in the core):
 *  the permission of «Segna come pagato» follows them as it follows money going out. */
const RESTRICTED_KINDS: Record<string, true> = {
  integration_vat: true,
  iva_settlement: true,
  withholding: true,
  tax_instalment: true,
  notice_plan: true,
  tax_balance: true,
  tax_advance: true,
  chamber_fee: true,
  tax_credit: true,
  payroll: true,
  cash_basis_vat: true,
  engagement: true,
}

/** Whether settling `item` is an administrator's act (design § 5, `lib/permissions.ts`). */
export function settlesAsAdmin(item: Pick<DueItem, 'direction' | 'source_kind'>): boolean {
  return item.direction === 'out' || RESTRICTED_KINDS[item.source_kind] === true
}

/** The kind of document an item comes from; a kind this build does not know reads «Altra voce»
 *  and is still drawn, with its own description (the server decides which kinds a viewer sees). */
export function sourceLabel(kind: string): string {
  return SOURCES[kind]?.label ?? 'Altra voce'
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

/** What an item names as its tax besides the income taxes of the «Tasse» page. */
const OTHER_TAX_NAMES: Record<string, string> = { chamber_fee: 'Diritto camerale' }

/** A tax by the name the «Tasse» page uses; a code it does not know is shown as it came. */
export function taxName(tax: string | null | undefined): string | null {
  if (!tax) return null
  return (TAX_LABELS as Record<string, string>)[tax] ?? OTHER_TAX_NAMES[tax] ?? tax
}
