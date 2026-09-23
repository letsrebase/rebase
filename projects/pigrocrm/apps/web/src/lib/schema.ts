import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from './api'
import { queryKeys } from './query'

/** The nine field types the dynamic-field system supports (mirrors `FieldType` in
 *  packages/core/src/pigrocrm/core/fields/types.py — the one place that would need
 *  a tenth entry added, never here first). */
export type FieldType =
  | 'text'
  | 'textarea'
  | 'number'
  | 'currency'
  | 'date'
  | 'select'
  | 'multiselect'
  | 'checkbox'
  | 'url'

/**
 * One custom field, exactly as `GET /api/schema/{entity_type}` puts it on the wire.
 *
 * Verified live rather than assumed: brought up the API against a throwaway
 * Postgres, created one field of every type on `customer`, and read the real
 * response. It matches `describe_specs` in
 * packages/core/src/pigrocrm/core/fields/dynamic.py field for field — `type`, not
 * `field_type` (that rename happens on purpose, in that one function), `options`
 * always an array (`[]` when the type has none), `required` a plain boolean:
 *
 *   { "key": "segmento", "label": "Segmento", "type": "select", "required": false,
 *     "options": ["Enterprise", "PMI", "Startup"] }
 *
 * `describe_specs` is also exactly what the MCP `describe_schema` tool calls, so
 * this type describes both adapters' idea of a field at once, not just this one.
 */
export interface FieldDefinition {
  key: string
  label: string
  type: FieldType
  required: boolean
  options: string[]
}

/** The field types whose control holds text, and whose column therefore clears on `""`.
 *  Everything else -- a number, a currency amount, a date, a checkbox, an id with its
 *  own picker -- holds a typed value that has no empty spelling. */
const TEXT_SHAPED: ReadonlySet<FieldType> = new Set(['text', 'textarea', 'url', 'select'])

/**
 * What a form must send for a *native* column the user just emptied.
 *
 * Two spellings, and which one is right depends on the column's type -- this is the
 * detail every form here got wrong in the same way. `""` was the whole answer while
 * `exclude_none=True` was the backend's update contract: an omitted key changed
 * nothing, `null` was silently discarded, and `""` was the only thing left that meant
 * "clear it". It only ever worked for text columns. On `valore_previsto`,
 * `data_chiusura_prevista`, `ore_preventivate` or `tariffa_applicata`, `""` is not a
 * decimal or a date and never reached the service at all -- Pydantic answered 422, so
 * an estimate typed once could not be taken back. That is residual A14, seen from this
 * side of the wire.
 *
 * Task 4B-1 closed it: `supplied_changes` reads `model_fields_set`, so a key supplied
 * as `null` now clears its column and an omitted key still changes nothing. Text
 * columns keep `""` -- plan 1B's contract, and the backend normalizes it to `NULL` for
 * the fields where the distinction matters (`_check_fiscal`, `_check_email`); every
 * other type clears on `null`.
 *
 * An unknown key (one the form renders with a control of its own rather than through
 * `NATIVE_FIELDS` -- `category_id` on a cost) gets `null`: those are ids, never text.
 *
 * A `null` aimed at a `NOT NULL` column comes back as the server's own
 * `ValidationFailed` naming the field, which `DynamicForm` shows on that very control.
 * That is deliberate, and better than the 422 it replaces: the rule belongs to the
 * server, and the message says which field and why.
 */
export function clearedNativeValue(fields: readonly FieldDefinition[], key: string): '' | null {
  const type = fields.find((field) => field.key === key)?.type
  return type !== undefined && TEXT_SHAPED.has(type) ? '' : null
}

export type EntityType =
  | 'customer'
  | 'person'
  | 'deal'
  | 'document'
  | 'invoice'
  | 'time_entry'
  | 'cost'
  | 'attivita'
  | 'contract'

/**
 * The subset of `EntityType` that has a real `GET /api/{plural}/{id}/timeline`
 * route -- what `Timeline.tsx`'s `TIMELINE_FETCHERS` and `EntityDetailLayout`'s
 * `entityType` prop accept. An entity can be a declared `EntityType` (so it carries
 * custom fields and appears in `/api/schema/{entity_type}`) long before any router
 * serves its timeline, and conflating the two sets would either exclude a real
 * working route or invent an endpoint that is not there.
 *
 * `invoice` was excluded while its router did not exist. `apps/api/.../invoices.py`
 * now serves `GET /api/invoices/{invoice_id}/timeline`, so the exclusion is gone --
 * widened *alongside* the endpoint, which is the rule this comment carried from the
 * day the alias was written.
 *
 * `time_entry`/`cost` are excluded the same way now: `TIMELINE_FETCHERS` is a
 * `Record<TimelineEntityType, ...>`, so a bare `= EntityType` alias would force an
 * entry for both the moment `EntityType` grew them, even though neither has a
 * timeline router yet. Widen this `Exclude` alongside the endpoint, not before it.
 *
 * `attivita` joined them on 2026-09-09: slice 10 writes `activities` rows for every
 * closure of a commitment, so the timeline *data* exists, and
 * `GET /api/activities/{id}/timeline` does not. Excluded until it does -- inventing the
 * route in this type would give `Timeline` a fetcher that 404s.
 *
 * `contract` joined them at REB-358 for the identical reason: `ContractService.create`
 * writes an activity row, and no `GET /api/contracts/{id}/timeline` route exists yet.
 */
export type TimelineEntityType = Exclude<EntityType, 'time_entry' | 'cost' | 'attivita' | 'contract'>

export interface EntitySchema {
  entity_type: string
  native_fields: string[]
  custom_fields: FieldDefinition[]
}

/**
 * Reads the live shape of an entity — the same document the MCP `describe_schema`
 * tool returns, so the UI and an agent can never disagree about what fields exist.
 *
 * The path is `/api/schema/{entity_type}`, not the bare `/schema/{entity_type}`:
 * every router in apps/api/src/pigrocrm_api/routers/*.py declares its own "/api"
 * prefix, and lib/api.ts's client is built with `baseUrl: ''` to match — confirmed
 * live, the bare path 404s.
 *
 * The cast below is not a shortcut: `apps/api/src/pigrocrm_api/routers/schema.py`
 * declares `EntitySchema.custom_fields` as `list[dict[str, Any]]`, so that's as far
 * as FastAPI's own OpenAPI document — and therefore openapi-typescript's generated
 * `components["schemas"]["EntitySchema"]` — can describe it: `{ [key: string]:
 * unknown }[]`, not the real per-field shape. `FieldDefinition` above is that real
 * shape, established by reading `describe_specs` and confirmed against the live
 * response, so this is where the gap between the generated type and reality gets
 * closed, once, for every caller of this hook.
 */
export function useEntitySchema(entityType: EntityType) {
  return useQuery({
    queryKey: queryKeys.schema(entityType),
    queryFn: async (): Promise<EntitySchema> => {
      const schema = await unwrap(
        api.GET('/api/schema/{entity_type}', { params: { path: { entity_type: entityType } } }),
      )
      return schema as unknown as EntitySchema
    },
  })
}
