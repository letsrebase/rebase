import { useQuery } from '@tanstack/react-query'
import { Bot, CircleHelp, Cog, DoorOpen, User, type LucideIcon } from 'lucide-react'
import { formatOccurredAtExact, humanize, labelForKind } from '@/components/activityLabels'
import { Badge } from '@rebase/ui/badge'
import { Skeleton } from '@rebase/ui/skeleton'
import { api, toProblem, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'
import type { TimelineEntityType } from '@/lib/schema'

/**
 * The wire shape of one timeline row, taken directly from the generated OpenAPI
 * schema (`components['schemas']['ActivityRead']`) rather than hand-declared: the
 * single source of truth is `ActivityRead` in
 * packages/core/src/pigrocrm/core/activities/schemas.py, and this alias tracks it
 * through `pnpm generate:api` automatically. A hand-written interface here would
 * silently drift the day that schema grows a field -- which the backend's own
 * docstring on `Activity` (activities/models.py) promises will happen ("later
 * slices append emails, documents, invoices and time entries by writing new `kind`
 * values -- no migration").
 *
 * Note `actor_type` comes through as a bare `string`, not the `'user' | 'mcp' |
 * 'system' | 'rebase'` literal union `ActorType` is declared as in
 * packages/core/src/pigrocrm/core/actor.py: `ActivityRead.actor_type` (schemas.py)
 * widens it to `str` before it ever reaches Pydantic's own JSON Schema export, so
 * openapi-typescript has nothing narrower to generate. `actorMeta` below treats
 * every value defensively for exactly this reason -- the type system does not
 * rule out a fifth value showing up on the wire.
 */
type ActivityEntry = components['schemas']['ActivityRead']

type ActorVariant = 'default' | 'secondary' | 'outline'

interface ActorMeta {
  label: string
  icon: LucideIcon
  variant: ActorVariant
}

/**
 * Keyed by the real `Actor.type` values (packages/core/src/pigrocrm/core/actor.py),
 * confirmed against a running instance of this API: a browser session records
 * "user"; a personal-access-token call -- the MCP server's own authentication,
 * pat_service.py -- records "mcp", tied to the human who owns the token, not to a
 * null actor; "system" is reserved for actor-less bootstrap actions (`Actor.
 * system()`, today only `pigrocrm createadmin`); "rebase" is the hub itself, writing
 * through the engagements door with the freelancer's own consent -- an admin, and
 * `Actor.rebase()`'s own docstring is explicit that it is never an agent, so it does
 * not share `mcp`'s treatment either (milestone A, REB-490).
 *
 * "did I do that, or did an agent?" is the first question this whole timeline exists to
 * answer, and since REB-781 so is "who, and from where": each entry carries the member's
 * name (`ActivityRead.actor_name`, resolved from `actor_id`, a deactivated member's
 * included) and a channel marker, a word and not only a glyph. The one the product most
 * wants noticed, a change an agent made on someone's behalf, gets «via MCP» in the bold
 * fill of a primary action; a browser change gets the quiet «dal web» beside it, so the
 * two are never told apart by the absence of a marker.
 */
const ACTOR_META: Record<string, ActorMeta> = {
  user: { label: 'Utente', icon: User, variant: 'outline' },
  mcp: { label: 'Agente AI', icon: Bot, variant: 'default' },
  system: { label: 'Sistema', icon: Cog, variant: 'secondary' },
  rebase: { label: 'rebase', icon: DoorOpen, variant: 'secondary' },
}

function actorMeta(actorType: string): ActorMeta {
  return (
    ACTOR_META[actorType] ?? { label: humanize(actorType), icon: CircleHelp, variant: 'outline' }
  )
}

/**
 * What a person reads as the channel of a change. `user` and `mcp` are the two a member can
 * make a change through, and each says so; `system` and `rebase` are named by the author
 * label itself and add nothing. Keyed by the same `Actor.type` values as `ACTOR_META`.
 */
const CHANNEL_LABEL: Record<string, string> = { user: 'dal web', mcp: 'via MCP' }

function ChannelBadge({ actorType }: { actorType: string }) {
  const label = CHANNEL_LABEL[actorType]
  if (label === undefined) return null
  return (
    <Badge variant={actorMeta(actorType).variant} className="shrink-0">
      {label}
    </Badge>
  )
}

/**
 * Who did it: the member's name, and the channel. A row with no name to give (`system`,
 * `rebase`, or an id no user row holds any more) falls back to the label of its actor type,
 * which is what the timeline said before names existed.
 */
function ActorLine({ entry }: { entry: ActivityEntry }) {
  const { label, icon: Icon } = actorMeta(entry.actor_type)
  return (
    <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
      <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
      <span className="font-medium">{entry.actor_name ?? label}</span>
      <ChannelBadge actorType={entry.actor_type} />
    </div>
  )
}

/**
 * An automation's entry is the system's, and names the member whose action set it off in
 * `payload.attivata_da`, with the channel that member used in `attivata_via` (§9.5). Both
 * are read here and kept out of the generic dump, which would print an id.
 */
function TriggeredBy({ entry }: { entry: ActivityEntry }) {
  const { attivata_da: member, attivata_via: via } = entry.payload
  if (member === undefined || member === null) return null
  const channel = typeof via === 'string' ? CHANNEL_LABEL[via] : undefined
  return (
    <p className="mt-1 text-sm text-muted-foreground">
      Attivata da{' '}
      <strong className="font-medium text-foreground">
        {entry.triggered_by_name ?? 'un membro rimosso'}
      </strong>
      {channel !== undefined ? ` (${channel})` : null}
    </p>
  )
}

/**
 * The one piece of detail worth a second line, per payload shape rather than per
 * `kind` string -- checked this way because the payload's *shape*, not the kind,
 * is what actually determines what there is to say, and a `kind` this build has
 * never seen can still carry a shape it recognises (or, worst case, falls through
 * to the generic dump below, never a crash, never `undefined`).
 *
 *  - `updated` writes `{"changed": [...]}` (customers/people/deals `service.py`).
 *  - `stage_changed` (deals only) writes `{"from": ..., "to": ...}`. Read by
 *    name, not by object key order: Postgres JSONB does not preserve insertion
 *    order (confirmed live -- the real API returned `{"to": ..., "from": ...}`
 *    for a payload the backend constructs as `{"from": ..., "to": ...}`), so
 *    anything that assumed the first key was always "from" would be reading the
 *    wrong side of the transition at random.
 *  - `created`'s payload is per-entity (`ragione_sociale` for a customer, `nome`
 *    for a person, `nome`+`stage` for a deal) and `deleted`/`restored` carry `{}`
 *    -- both land in the generic fallback, which is `null` for the empty case
 *    (the label above already says everything there is to say) and an
 *    ugly-but-true `snake_case: value` dump otherwise, which beats saying
 *    nothing about a real event.
 */
function ActivityDetail({ payload }: { payload: ActivityEntry['payload'] }) {
  if (Array.isArray(payload.changed) && payload.changed.length > 0) {
    return (
      <p className="mt-1 text-sm text-muted-foreground">
        Campi modificati: {payload.changed.map((field) => humanize(String(field))).join(', ')}
      </p>
    )
  }

  if ('from' in payload && 'to' in payload) {
    return (
      <p className="mt-1 text-sm text-muted-foreground">
        Da <strong className="font-medium text-foreground">{String(payload.from)}</strong> a{' '}
        <strong className="font-medium text-foreground">{String(payload.to)}</strong>
      </p>
    )
  }

  // `importata_da` is dropped rather than dumped: the `imported` label already says the
  // invoice was issued elsewhere, and the column's value names the system it came from
  // -- provenance the CRM keeps for itself, never product copy (`invoices/models.py`).
  // The rest of that payload (anno, numero, totale) are facts about the document and
  // stay. Filtered by key here, in the generic branch, so the same is true of any
  // future `kind` that records the same field.
  // The ids a payload carries to say which draft or which member it concerns are for the
  // machine: dumping a UUID tells a reader nothing the label does not.
  const hidden: Record<string, true> = {
    importata_da: true,
    attivata_da: true,
    attivata_via: true,
    draft_id: true,
    user_id: true,
  }
  const rest = Object.entries(payload).filter(([key]) => hidden[key] !== true)
  if (rest.length === 0) return null
  return (
    <p className="mt-1 text-sm text-muted-foreground">
      {rest.map(([key, value]) => `${humanize(key)}: ${String(value)}`).join(' · ')}
    </p>
  )
}

// Three explicit branches, not a shared helper keyed off a path template: the
// path parameter *name* differs per entity (customer_id/person_id/deal_id), and
// that name is exactly what a generated client type-checks against the live
// OpenAPI document. This is the "small discriminated union" alternative to the
// original plan's `api.GET(PATHS[entityType] as never, { params: { ... } as
// never })` -- every branch below is still checked against `paths` in
// lib/api-types.ts, so a renamed path or parameter fails `tsc`, not silently at
// runtime the way the two `as never` casts would have.
const TIMELINE_FETCHERS: Record<
  TimelineEntityType,
  (entityId: string, limit: number) => Promise<ActivityEntry[]>
> = {
  customer: (entityId, limit) =>
    unwrap(
      api.GET('/api/customers/{customer_id}/timeline', {
        params: { path: { customer_id: entityId }, query: { limit } },
      }),
    ),
  person: (entityId, limit) =>
    unwrap(
      api.GET('/api/people/{person_id}/timeline', {
        params: { path: { person_id: entityId }, query: { limit } },
      }),
    ),
  deal: (entityId, limit) =>
    unwrap(
      api.GET('/api/deals/{deal_id}/timeline', {
        params: { path: { deal_id: entityId }, query: { limit } },
      }),
    ),
  document: (entityId, limit) =>
    unwrap(
      api.GET('/api/documents/{document_id}/timeline', {
        params: { path: { document_id: entityId }, query: { limit } },
      }),
    ),
  invoice: (entityId, limit) =>
    unwrap(
      api.GET('/api/invoices/{invoice_id}/timeline', {
        params: { path: { invoice_id: entityId }, query: { limit } },
      }),
    ),
  contract: (entityId, limit) =>
    unwrap(
      api.GET('/api/contracts/{contract_id}/timeline', {
        params: { path: { contract_id: entityId }, query: { limit } },
      }),
    ),
}

interface TimelineProps {
  entityType: TimelineEntityType
  entityId: string
  /**
   * The backend's own bound (routers/{customers,people,deals}.py's shared
   * `Query(ge=1, le=200)`, mirrored again in `ActivityService.timeline` itself so
   * the MCP `get_timeline` tool -- which calls that method directly, with no REST
   * schema in between -- cannot bypass it either): asking for more than 200 or
   * fewer than 1 gets a 422, not a silently clamped value. This default only
   * matches the backend's own default of 50 when the prop is omitted; it does not
   * re-enforce the bound client-side, so a caller that passes an out-of-range
   * value sees the same validation error curl would get, through the usual
   * `toProblem` path, rather than having it masked here.
   */
  limit?: number
}

/**
 * One entry per row: what happened (`kind`, humanized if this build does not recognise it
 * yet), when (the exact Italian date and time, seconds included), who did it (the member's
 * name) and from where (a «dal web» or «via MCP» marker, never an absence of one), and
 * whatever extra the payload has to say.
 */
export function Timeline({ entityType, entityId, limit = 50 }: TimelineProps) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: [...queryKeys.timeline(entityType, entityId), limit],
    queryFn: () => TIMELINE_FETCHERS[entityType](entityId, limit),
  })

  if (isLoading) {
    return (
      <div className="space-y-3" role="status" aria-label="Caricamento timeline">
        {Array.from({ length: 4 }, (_, index) => (
          <Skeleton key={index} className="h-16 w-full" />
        ))}
      </div>
    )
  }

  // A failed request is not the same claim as "nothing happened yet" -- showing
  // the empty state here would be a confident lie about a request that never
  // actually completed. `toProblem` accepts the raw `unknown` react-query hands
  // back (see its own docstring: the query fn above only ever rejects with an
  // already-normalised `ProblemDetail`, via `unwrap`) and gives back the same
  // honest Italian message every other failure path in this app shows.
  if (isError) {
    return <p className="text-sm text-destructive">{toProblem(error).detail}</p>
  }

  if (!data || data.length === 0) {
    return <p className="text-muted-foreground">Nessuna attività registrata.</p>
  }

  return (
    <ol className="space-y-3">
      {data.map((entry) => (
        <li key={entry.id} className="border p-3">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <span className="font-medium">{labelForKind(entry.kind)}</span>
            <time dateTime={entry.occurred_at} className="shrink-0 text-xs text-muted-foreground">
              {formatOccurredAtExact(entry.occurred_at)}
            </time>
          </div>
          <ActorLine entry={entry} />
          <TriggeredBy entry={entry} />
          <ActivityDetail payload={entry.payload} />
        </li>
      ))}
    </ol>
  )
}
