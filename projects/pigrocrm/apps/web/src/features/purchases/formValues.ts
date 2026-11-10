import { toIsoDate } from '@/lib/dates'
import { toFormValues, toRequestBody, type FormValues } from '@/lib/formValues'
import type { FieldDefinition } from '@/lib/schema'
import type { Purchase, PurchaseKind } from './queries'

/**
 * The value layer behind `PurchaseForm`, split out of it for the reason
 * `features/time/formValues.ts` was: `react-refresh/only-export-components` is live on
 * `src/features/**`, so the form module exports its component and nothing else.
 *
 * Money stays a string from the input to the body: nothing here parses a decimal.
 */

export const KIND_LABELS: Record<PurchaseKind, string> = {
  invoice: 'Fattura',
  credit_note: 'Nota di credito',
  receipt: 'Ricevuta',
  expense: 'Spesa',
  expense_note: 'Nota spese',
}

/** The kinds a person registers by hand. An expense note is written by its approval (REB-1046) and
 *  never typed, so the form does not offer it. */
export const KINDS: readonly PurchaseKind[] = ['invoice', 'credit_note', 'receipt', 'expense']

/** The kinds the list can be filtered by: every one a purchase can be, an expense note included. */
export const FILTER_KINDS = Object.keys(KIND_LABELS) as PurchaseKind[]

/** An invoice or a credit note: a fiscal document with a supplier and a VAT recap. A
 *  receipt or an expense has its total only. */
export function isDocumentKind(kind: PurchaseKind): boolean {
  return kind === 'invoice' || kind === 'credit_note'
}

/** One row of the VAT recap as the form holds it: every figure is the text typed. */
export interface VatLineDraft {
  rate: string
  nature: string
  taxable_amount: string
  vat_amount: string
  deductible_share: string
}

export function emptyVatLine(): VatLineDraft {
  return { rate: '22', nature: '', taxable_amount: '', vat_amount: '', deductible_share: '100' }
}

/**
 * Whether a rate typed as text is zero, which is when the recap asks for the nature
 * (the reason a zero-rate line carries no VAT). Read off the digits, not parsed: a rate
 * is a decimal and a float comparison is exactly what `no-float-money.test.ts` exists to stop.
 */
export function isZeroRate(rate: string): boolean {
  const text = rate.trim()
  return /\d/.test(text) && /^0*(\.0*)?$/.test(text)
}

const FIELD = (
  key: string,
  label: string,
  type: FieldDefinition['type'],
  required = false,
): FieldDefinition => ({ key, label, type, required, options: [] })

const F = {
  merchant_name: FIELD('merchant_name', 'Esercente', 'text'),
  number: FIELD('number', 'Numero', 'text'),
  date: FIELD('date', 'Data documento', 'date', true),
  received_on: FIELD('received_on', 'Ricevuta il', 'date'),
  iva_period: FIELD('iva_period', 'Periodo IVA (un giorno del periodo)', 'date'),
  description: FIELD('description', 'Descrizione', 'textarea', true),
  amount: FIELD('amount', 'Importo (€)', 'currency', true),
  currency: FIELD('currency', 'Valuta', 'text'),
  currency_amount: FIELD('currency_amount', 'Importo in valuta', 'currency'),
  exchange_rate: FIELD('exchange_rate', 'Cambio', 'number'),
  exchange_rate_date: FIELD('exchange_rate_date', 'Data del cambio', 'date'),
  stamp_duty: FIELD('stamp_duty', 'Bollo (€)', 'currency'),
  withholding_rate: FIELD('withholding_rate', 'Ritenuta (%)', 'number'),
  withholding_base: FIELD('withholding_base', 'Base della ritenuta (€)', 'currency'),
  withholding_amount: FIELD('withholding_amount', 'Ritenuta (€)', 'currency'),
  fund_rate: FIELD('fund_rate', 'Cassa previdenziale (%)', 'number'),
  fund_amount: FIELD('fund_amount', 'Cassa previdenziale (€)', 'currency'),
  split_payment: FIELD('split_payment', 'Scissione dei pagamenti', 'checkbox'),
  reverse_charge: FIELD('reverse_charge', 'Inversione contabile', 'checkbox'),
}

const FOREIGN_KEYS = ['currency_amount', 'exchange_rate', 'exchange_rate_date'] as const

/** In euro, or with no currency typed yet: the foreign block applies to neither. */
export function isForeignCurrency(currency: unknown): boolean {
  const text = typeof currency === 'string' ? currency.trim().toUpperCase() : ''
  return text !== '' && text !== 'EUR'
}

/**
 * What the form shows of a document's optional parts. Every part defaults to shown, so a caller
 * that knows nothing yet (the capabilities are still loading, the supplier is not read) hides
 * nothing: a form that waits is a form nobody can fill in.
 */
export interface FieldVisibility {
  /** The VAT recap and the VAT period. */
  vatRecap?: boolean
  /** The withholding block: bollo, ritenuta, cassa. */
  withholding?: boolean
  reverseCharge?: boolean
  splitPayment?: boolean
}

/**
 * The native fields the form draws for a kind, in the order it draws them.
 *
 * What a kind does not take is not drawn, and `toPurchaseRequestBody` does not send it
 * either: the same list is both, and so is what `shown` takes away. The foreign-currency block
 * appears when the currency is not the euro -- all four values travel together or none does.
 */
export function fieldsFor(
  kind: PurchaseKind,
  foreign: boolean,
  shown: FieldVisibility = {},
): FieldDefinition[] {
  const foreignBlock = foreign ? FOREIGN_KEYS.map((key) => F[key]) : []
  if (isDocumentKind(kind)) {
    return [
      F.number,
      F.date,
      F.received_on,
      ...(shown.vatRecap === false ? [] : [F.iva_period]),
      F.description,
      F.amount,
      F.currency,
      ...foreignBlock,
      ...(shown.withholding === false
        ? []
        : [
            F.stamp_duty,
            F.withholding_rate,
            F.withholding_base,
            F.withholding_amount,
            F.fund_rate,
            F.fund_amount,
          ]),
      ...(shown.splitPayment === false ? [] : [F.split_payment]),
      ...(shown.reverseCharge === false ? [] : [F.reverse_charge]),
    ]
  }
  return [F.merchant_name, F.number, F.date, F.description, F.amount, F.currency, ...foreignBlock]
}

/** The supplier kinds whose invoices can carry a withholding, a pension fund and a stamp
 *  duty: a professional and an occasional worker. The API's `supplier_kind` is free text
 *  (`null` for a company or a foreign supplier), so this is the one place that names the
 *  two; no component compares a regime or a pack. */
export function carriesWithholding(supplierKind: string | null | undefined): boolean {
  return supplierKind === 'professional' || supplierKind === 'occasional'
}

/** Every native field of the form, whatever the kind: the clearing spelling of a column
 *  (`clearedNativeValue`) reads its type from here. */
const ALL_FIELDS: FieldDefinition[] = Object.values(F)

/**
 * The keys with a control of their own (a picker, the VAT editor), not drawn by
 * `DynamicForm`: `category_id` and `supplier_id` are ids, and `FieldDefinition.options` is
 * `string[]`, so a `select` field would submit a name where the API wants a UUID.
 * `legal_entity_id` likewise (REB-626): drawn only for a purchase with no deal, where
 * `null` is the «Condivisa» the server reads as a shared purchase.
 */
export const PICKER_KEYS = [
  'kind',
  'category_id',
  'supplier_id',
  'legal_entity_id',
  'corrects_purchase_id',
  'vat_lines',
] as const

const NATIVE_FIELD_KEYS = [...ALL_FIELDS.map((field) => field.key), ...PICKER_KEYS]

/** The form's state in the API's two namespaces -- see `FormValues`. `native.vat_lines` is
 *  a `VatLineDraft[]`. */
export type PurchaseFormValues = FormValues

/** Today, from the local calendar parts: `toISOString().slice(0, 10)` converts to UTC
 *  first, so east of Greenwich late in the evening the dialog would open on tomorrow. */
export function defaultPurchaseFormValues(today: Date = new Date()): PurchaseFormValues {
  return {
    native: {
      kind: 'expense',
      date: toIsoDate(today),
      currency: 'EUR',
      vat_lines: [],
      split_payment: false,
      reverse_charge: false,
    },
    custom: {},
  }
}

/**
 * The starting state of «Nuovo acquisto». A supplier already chosen, or a file from «In arrivo»
 * to register, is a document: it opens on an invoice with its first VAT row, since that is
 * what a file from a supplier is; with neither it is the blank form, an expense.
 */
export function newPurchaseFormValues(
  defaults: { supplier_id?: string; inbox_item_id?: string } = {},
  today: Date = new Date(),
): PurchaseFormValues {
  const blank = defaultPurchaseFormValues(today)
  if (defaults.supplier_id === undefined && defaults.inbox_item_id === undefined) return blank
  return {
    ...blank,
    native: {
      ...blank.native,
      kind: 'invoice',
      supplier_id: defaults.supplier_id ?? null,
      vat_lines: [emptyVatLine()],
    },
  }
}

/**
 * The form's starting state from a purchase that already exists.
 *
 * Walks exactly `NATIVE_FIELD_KEYS` rather than spreading the row: a `PurchaseRead` also
 * carries `id`, `status`, `cost_amount`, `deal_id` and the frozen buyer pack, none of which
 * this form is allowed to send back -- `deal_id` above all, since moving a purchase to
 * another deal is not something this screen offers and a stray one would silently
 * reattribute it.
 */
export function purchaseToFormValues(purchase: Purchase): PurchaseFormValues {
  const values = toFormValues(purchase, NATIVE_FIELD_KEYS)
  values.native.vat_lines = purchase.vat_lines.map((line) => ({
    rate: line.rate,
    nature: line.nature ?? '',
    taxable_amount: line.taxable_amount,
    vat_amount: line.vat_amount,
    deductible_share: line.deductible_share,
  }))
  return values
}

/** The recap as the API takes it: rows left wholly blank are dropped, and `nature` rides
 *  only on a zero-rate line (the server refuses it on any other). */
export function cleanVatLines(lines: readonly VatLineDraft[]) {
  return lines
    .filter((line) => [line.taxable_amount, line.vat_amount].some((value) => value.trim() !== ''))
    .map((line) => ({
      rate: line.rate.trim(),
      ...(isZeroRate(line.rate) && line.nature.trim() !== '' ? { nature: line.nature.trim() } : {}),
      taxable_amount: line.taxable_amount.trim(),
      vat_amount: line.vat_amount.trim(),
      deductible_share: line.deductible_share.trim() === '' ? '100' : line.deductible_share.trim(),
    }))
}

/**
 * On an edit, what the person did not change is not sent: a PATCH that echoes every column
 * is one the server must read as «maybe a move», and a purchase whose period is closed
 * refuses a write that touches a register column even when the value is the one it holds.
 * The currency and its foreign block travel together or not at all, so one of them changing
 * keeps the rest. `custom_fields` is left as `toRequestBody` built it.
 */
function dropUnchanged(body: Record<string, unknown>, initial: Record<string, unknown>) {
  const changed = (key: string) => !(key in initial) || body[key] !== initial[key]
  const currencyMoved = ['currency', ...FOREIGN_KEYS].some((key) => key in body && changed(key))
  for (const key of Object.keys(body)) {
    if (key === 'custom_fields') continue
    if (currencyMoved && (key === 'currency' || (FOREIGN_KEYS as readonly string[]).includes(key))) {
      continue
    }
    if (!changed(key)) delete body[key]
  }
}

function draftsOf(value: unknown): VatLineDraft[] {
  return Array.isArray(value) ? (value as VatLineDraft[]) : []
}

/**
 * Turns the two namespaces into the body the API takes.
 *
 * - Only what the kind takes is sent: switching from an invoice to an expense in the open
 *   dialog does not carry the supplier or the recap along.
 * - The `kind` is sent on create only. An edit keeps it: a receipt does not become an
 *   invoice by editing, it is registered again.
 * - The foreign-currency block is sent when the currency is not the euro, and cleared when
 *   the purchase used to hold one and is now in euro.
 * - The VAT recap replaces the whole stored one, so it is sent only when it changed.
 * - A native column clears on the spelling its type calls for (`toRequestBody`); the ids
 *   with a picker of their own clear on `null` because an id is never text. `amount` and
 *   `description` are `NOT NULL`, so emptying one comes back as the server's own refusal
 *   naming the field, shown on that control instead of a bare 422.
 */
export function toPurchaseRequestBody(
  values: PurchaseFormValues,
  options: {
    initial?: PurchaseFormValues
    isRenderedCustomKey: (key: string) => boolean
    /** What the form drew of a document's optional parts; what is not drawn is not sent. All
     *  of it when left out. */
    shown?: FieldVisibility
  },
): Record<string, unknown> {
  const kind = (values.native.kind as PurchaseKind | undefined) ?? 'expense'
  const foreign = isForeignCurrency(values.native.currency)
  const drawn = new Set(
    fieldsFor(kind, foreign, options.shown).map((field) => field.key),
  )
  const document = isDocumentKind(kind)

  const sent = (key: string): boolean => {
    if (drawn.has(key)) return true
    switch (key) {
      case 'category_id':
      case 'legal_entity_id':
        return true
      case 'supplier_id':
        return document
      case 'corrects_purchase_id':
        return kind === 'credit_note'
      default:
        return false
    }
  }

  // A foreign block left behind in the state by a currency switched back to euro is
  // blanked here, so `toRequestBody` clears it when the row used to hold it.
  const native = foreign
    ? values.native
    : { ...values.native, ...Object.fromEntries(FOREIGN_KEYS.map((key) => [key, null])) }
  const initialNative = options.initial?.native
  const body = toRequestBody(
    { ...values, native },
    {
      fields: ALL_FIELDS,
      initial: options.initial,
      isRenderedCustomKey: options.isRenderedCustomKey,
      skipNative: (key) => !sent(key) && !(initialNative && FOREIGN_KEYS.some((k) => k === key)),
      coerceNative: (key, value) =>
        key === 'currency' && typeof value === 'string' ? value.trim().toUpperCase() : value,
    },
  )

  if (options.initial === undefined) body.kind = kind
  else dropUnchanged(body, options.initial.native)

  if (document && options.shown?.vatRecap !== false) {
    const lines = cleanVatLines(draftsOf(values.native.vat_lines))
    const before = cleanVatLines(draftsOf(initialNative?.vat_lines))
    if (options.initial === undefined || JSON.stringify(lines) !== JSON.stringify(before)) {
      body.vat_lines = lines
    }
  }
  return body
}
