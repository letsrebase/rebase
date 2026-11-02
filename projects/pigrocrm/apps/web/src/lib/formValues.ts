import { clearedNativeValue, type FieldDefinition } from './schema'

/**
 * The value layer every entity form (customer, person, deal, cost, time entry) shares:
 * the form's state, the starting state of an edit, and the request body built back out of
 * it. Each entity keeps only what is its own -- its field table, the keys that have a
 * picker of their own, the lines it adds to the body -- and none of the rules below.
 */

/**
 * The form's state, kept in the two namespaces the API itself uses -- native columns at
 * the top level of the request body, custom fields inside `custom_fields` -- and never
 * flattened into one.
 *
 * Flattening was a real, reproduced data bug, not a style question. A flat bag has to
 * re-derive, at submit time, which keys were custom, and the only thing available to
 * re-derive it from is the *currently active* schema. Archive a field definition while a
 * record still holds a value for it and that key drops out of the active schema: the value
 * is still in the form, but it is now classified as native and sent as a top-level key.
 * The update schemas are `extra="forbid"`, so the PATCH 422s on an edit where the user
 * changed nothing and the record stays uneditable.
 *
 * Provenance is therefore structural: a value that came out of `custom_fields` stays in
 * `custom` for as long as the dialog is open, whatever happens to its definition, and
 * nothing re-asks the schema what it is.
 */
export interface FormValues {
  /** Editable native columns, exactly the keys the entity seeds. */
  native: Record<string, unknown>
  /** Everything stored under `custom_fields`, including keys whose definition has since
   *  been archived and which therefore render nowhere. */
  custom: Record<string, unknown>
}

/** Mirrors `is_blank` in packages/core/src/pigrocrm/core/fields/validator.py:
 *  `null`/`undefined`, a whitespace-only string, or an empty array all mean "no value" --
 *  `false` and `0` do not, they are real values. The client has to draw the same line the
 *  server does: two definitions of "empty" is how a required check and a
 *  clear-vs-untouched check start disagreeing. */
export function isBlank(value: unknown): boolean {
  if (value === null || value === undefined) return true
  if (typeof value === 'string') return value.trim() === ''
  if (Array.isArray(value)) return value.length === 0
  return false
}

/**
 * The starting state of an edit, from a record that already exists.
 *
 * Walks exactly `nativeKeys` rather than spreading the row: a `*Read` also carries `id`,
 * `created_at` and other keys the `*Update` schema (`extra="forbid"`) refuses, so sending
 * them back would 422 on every edit. `custom_fields` is copied whole into its own
 * namespace, archived keys included.
 */
export function toFormValues(
  entity: { custom_fields?: object | null },
  nativeKeys: readonly string[],
): FormValues {
  const row = entity as unknown as Record<string, unknown>
  const native: Record<string, unknown> = {}
  for (const key of nativeKeys) {
    native[key] = row[key]
  }
  return { native, custom: { ...entity.custom_fields } }
}

interface RequestBodyOptions {
  /** The entity's native `FieldDefinition`s: the column's type decides how a cleared
   *  value is spelled (`clearedNativeValue`). */
  fields: readonly FieldDefinition[]
  /** The state the form opened with; absent on create. */
  initial?: FormValues
  /** True for a key the active schema currently renders as a custom-field control. */
  isRenderedCustomKey: (key: string) => boolean
  /** Native keys the entity handles on its own (a picker's id, a frozen column): they
   *  are neither sent nor cleared here. */
  skipNative?: (key: string) => boolean
  /** The value to send for a non-blank native column, when it is not the typed one. */
  coerceNative?: (key: string, value: unknown) => unknown
}

/**
 * The body the API takes, out of the two namespaces.
 *
 * A non-blank value is sent; a blank one is sent only when the record used to hold a value
 * -- the user cleared it, and an omitted key would leave the old one untouched. How
 * "cleared" is spelled depends on the namespace: a native column by its type
 * (`clearedNativeValue`: `""` for text, `null` for everything else), a custom field
 * only by an explicit `null` (`""` is `is_blank` there, which a non-required field just
 * skips, leaving the stored value in place). A custom value whose definition is no longer
 * active is omitted, and the server carries the stored value over untouched: echoing it
 * back is a 422 ("campo non definito") and `null` deletes it.
 */
export function toRequestBody(
  values: FormValues,
  { fields, initial, isRenderedCustomKey, skipNative, coerceNative }: RequestBodyOptions,
): Record<string, unknown> {
  const native: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(values.native)) {
    if (skipNative?.(key)) continue
    if (!isBlank(value)) {
      native[key] = coerceNative ? coerceNative(key, value) : value
    } else if (!isBlank(initial?.native[key])) {
      native[key] = clearedNativeValue(fields, key)
    }
    // else: blank now, blank (or never set) before -- nothing changed, nothing to say.
  }

  const custom: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(values.custom)) {
    if (!isRenderedCustomKey(key)) continue
    if (!isBlank(value)) {
      custom[key] = value
    } else if (!isBlank(initial?.custom[key])) {
      custom[key] = null
    }
  }

  return { ...native, custom_fields: custom }
}

/**
 * A form's loosely-typed body as the generated request type of the endpoint it goes to.
 *
 * The body is a dynamic mix of native columns and tenant-defined custom fields that no
 * fixed interface can describe, so the generated client's static body type can only be
 * met by a cast. It lives here, named, rather than as an `as unknown as XBody` at every
 * call site.
 */
export function asBody<T>(body: Record<string, unknown>): T {
  return body as unknown as T
}
