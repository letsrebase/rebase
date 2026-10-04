import { isBlank } from '@/features/time/formValues'
import { toIsoDate } from '@/lib/dates'
import { clearedNativeValue, type FieldDefinition } from '@/lib/schema'
import type { Cost } from './queries'

/**
 * The value layer behind `CostForm`, split out of it for the reason
 * `features/time/formValues.ts` was: `react-refresh/only-export-components` is live on
 * `src/features/**`, so the dialog module exports its component and nothing else.
 *
 * `isBlank` is imported rather than written again -- it is already exported from the
 * time feature, and this codebase has five copies of those four lines too many.
 */
export const NATIVE_FIELDS: FieldDefinition[] = [
  { key: 'data', label: 'Data', type: 'date', required: true, options: [] },
  { key: 'importo', label: 'Importo (€)', type: 'currency', required: true, options: [] },
  { key: 'descrizione', label: 'Descrizione', type: 'textarea', required: true, options: [] },
  { key: 'fornitore', label: 'Fornitore', type: 'text', required: false, options: [] },
]

/**
 * `category_id` is deliberately absent from `NATIVE_FIELDS` and gets its own control in
 * the dialog.
 *
 * The brief asked for it as a `select` `FieldDefinition`, and that cannot work:
 * `FieldDefinition.options` is `string[]`, and `DynamicFieldRenderer` submits the option
 * string itself as the value -- so the form would post a category *name* where
 * `CostCreate.category_id` requires a UUID, and two categories that happen to share a
 * name would be indistinguishable on the way back. A real picker keyed on the id is the
 * only shape that survives a rename.
 */
// `azienda_id` likewise (REB-626): a picker keyed on the id, drawn only for a cost with
// no deal, where `null` is the «Condivisa» the server reads as a shared expense.
const NATIVE_FIELD_KEYS = [...NATIVE_FIELDS.map((field) => field.key), 'category_id', 'azienda_id']

/** Two namespaces, decided once at seed time and never re-derived at submit -- the same
 *  structural provenance `TimeEntryFormValues` carries: a native column clears on the
 *  spelling its type calls for (`clearedNativeValue`), a custom field clears on `null`
 *  and only on `null`, and an omitted key clears nothing. */
export interface CostFormValues {
  native: Record<string, unknown>
  custom: Record<string, unknown>
}

/** Today, from the local calendar parts: `toISOString().slice(0, 10)` converts to UTC
 *  first, so east of Greenwich late in the evening the dialog would open on tomorrow. */
export function defaultCostFormValues(today: Date = new Date()): CostFormValues {
  return { native: { data: toIsoDate(today) }, custom: {} }
}

/**
 * The form's starting state from a cost that already exists.
 *
 * Walks exactly `NATIVE_FIELD_KEYS` rather than spreading the row: a `CostRead` also
 * carries `id`, `created_at`, `updated_at` and `deal_id`, none of which this form is
 * allowed to send back -- `deal_id` above all, since moving a cost to another deal is
 * not something this screen offers and a stray one would silently reattribute it.
 */
export function costToFormValues(cost: Cost): CostFormValues {
  const native: Record<string, unknown> = {}
  for (const key of NATIVE_FIELD_KEYS) {
    native[key] = (cost as unknown as Record<string, unknown>)[key]
  }
  return { native, custom: { ...cost.custom_fields } }
}

/**
 * Turns the two namespaces into the body the API takes.
 *
 * No `locked` parameter, unlike the time entry's: a cost has no frozen-when-billed
 * state -- nothing on an invoice points at one -- so there is no set of keys to drop.
 */
export function toCostRequestBody(
  values: CostFormValues,
  options: { initial?: CostFormValues; isRenderedCustomKey: (key: string) => boolean },
): Record<string, unknown> {
  const { initial, isRenderedCustomKey } = options

  const native: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(values.native)) {
    if (!isBlank(value)) {
      native[key] = value
    } else if (!isBlank(initial?.native[key])) {
      // The user cleared a native column that held a value: say so explicitly, or
      // the old value survives untouched. `clearedNativeValue` picks the spelling
      // from the column's type -- `descrizione` and `fornitore` clear on `""`, `data`
      // and `importo` on `null`, and `category_id` (which has its own picker and is
      // not in `NATIVE_FIELDS`) falls through to `null` because an id is never text.
      // All three of those are `NOT NULL`, so emptying one comes back as the server's
      // own refusal naming the field -- for `importo`, "l'importo non può essere
      // svuotato" -- shown on that control instead of a bare 422.
      native[key] = clearedNativeValue(NATIVE_FIELDS, key)
    }
  }

  const custom: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(values.custom)) {
    // A stored value whose definition is archived: omit the key and
    // `_update_custom_fields` carries it over untouched.
    if (!isRenderedCustomKey(key)) continue
    if (!isBlank(value)) {
      custom[key] = value
    } else if (!isBlank(initial?.custom[key])) {
      custom[key] = null
    }
  }

  return { ...native, custom_fields: custom }
}
