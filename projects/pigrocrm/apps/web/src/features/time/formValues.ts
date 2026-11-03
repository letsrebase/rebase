import { toIsoDate } from '@/lib/dates'
import {
  toFormValues,
  toRequestBody as buildRequestBody,
  type FormValues,
} from '@/lib/formValues'
import type { FieldDefinition } from '@/lib/schema'
import type { TimeEntry } from './queries'

/**
 * The editable native columns, as `FieldDefinition`s so `DynamicFieldRenderer` draws
 * them -- the same idiom `DealForm`/`CustomerForm` use. `deal_id` is absent: it comes
 * from the route, and reassigning an hour to another deal is not something this screen
 * offers. `user_id` is absent too: it is the logged-in user on create, and reassigning
 * it is an admin operation with no picker control (`FieldType` has no user type).
 */
export const NATIVE_FIELDS: FieldDefinition[] = [
  { key: 'data', label: 'Data', type: 'date', required: true, options: [] },
  { key: 'hours', label: 'Ore', type: 'number', required: true, options: [] },
  { key: 'description', label: 'Descrizione', type: 'textarea', required: true, options: [] },
  { key: 'billable', label: 'Fatturabile', type: 'checkbox', required: false, options: [] },
  {
    key: 'applied_rate',
    label: 'Tariffa (€/h, lascia vuoto per usare quella predefinita)',
    type: 'number',
    required: false,
    options: [],
  },
  { key: 'note_interne', label: 'Note interne', type: 'textarea', required: false, options: [] },
]

const NATIVE_FIELD_KEYS = NATIVE_FIELDS.map((field) => field.key)

/** Fields the backend freezes once the entry is on an issued invoice (§4.3 --
 *  `FROZEN_WHEN_BILLED` in `timetracking/service.py`). The form hides them rather than
 *  letting the user type into a control whose value the server will refuse: an
 *  `ImmutableField` after pressing Salva is a worse explanation than a field that is
 *  simply not there. */
export const LOCKED_KEYS = new Set([
  'data',
  'hours',
  'description',
  'billable',
  'applied_rate',
])

/** The form's state in the API's two namespaces -- see `FormValues`. */
export type TimeEntryFormValues = FormValues

/**
 * Today's date, and billable, because that is what somebody opening "Registra ore" is
 * almost always recording. Built from the local calendar parts rather than
 * `new Date().toISOString().slice(0, 10)`: `toISOString` converts to UTC first, so
 * anywhere east of Greenwich late in the evening the form would open pre-filled with
 * *tomorrow* -- and `_check_not_future` refuses a future date, so the user would meet a
 * server error on a date they never chose. The mirror image of the read-side trap
 * `formatIsoDateItalian` (@/lib/dates) closes.
 */
export function defaultFormValues(today: Date = new Date()): TimeEntryFormValues {
  return { native: { billable: true, data: toIsoDate(today) }, custom: {} }
}

/**
 * Builds the form's starting state from an entry that already exists.
 *
 * Deliberately not `{ ...entry, ...entry.custom_fields }`: a `TimeEntryRead` also
 * carries `id`, `rate_source`, `line_value`, `invoice_line_id`, `created_at` and
 * the rest, none of which `TimeEntryUpdate` accepts -- sending them back would 422 on
 * every edit. Walking exactly `NATIVE_FIELD_KEYS` keeps the round trip to what the form
 * is allowed to send; `custom_fields` is copied whole into its own namespace, archived
 * keys included, so `_update_custom_fields` can carry them over untouched.
 */
export function timeEntryToFormValues(entry: TimeEntry): TimeEntryFormValues {
  return toFormValues(entry, NATIVE_FIELD_KEYS)
}

/**
 * Turns the two namespaces into the body the API takes.
 *
 * A plain module function, not a method on the form, so `TimeEntryForm.tsx` exports
 * only its component: `react-refresh/only-export-components` is live on
 * `src/features/**`, and the three older forms each bought their way out with a
 * per-file eslint `allowExportNames` override. Splitting the value layer off instead is
 * the same coupling made explicit rather than waived -- `NATIVE_FIELDS`, the locked
 * keys and the payload rules are one thing, and the dialog that renders them is
 * another.
 */
export function toRequestBody(
  values: TimeEntryFormValues,
  options: { initial?: TimeEntryFormValues; locked: boolean; isRenderedCustomKey: (key: string) => boolean },
): Record<string, unknown> {
  const { initial, locked, isRenderedCustomKey } = options
  // Clearing a rate is the one native clear that matters most here: it is a deliberate
  // "this hour has no price", and `TimeEntryService.update` records it as
  // `origine = "absent"` rather than letting the deal's rate quietly take over.
  return buildRequestBody(values, {
    fields: NATIVE_FIELDS,
    initial,
    isRenderedCustomKey,
    skipNative: (key) => locked && LOCKED_KEYS.has(key),
  })
}
