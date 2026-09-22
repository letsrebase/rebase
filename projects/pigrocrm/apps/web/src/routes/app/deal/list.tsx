import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { Handshake, LayoutGrid, Search } from 'lucide-react'
import { useState } from 'react'
import { DataTable } from '@/components/DataTable'
import { FilterChips, FilterRow } from '@/components/FilterRow'
import { PageHeader } from '@/components/PageHeader'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { buildDealColumns } from '@/features/deals/columns'
import { useDeals, type DealsListParams } from '@/features/deals/queries'
import { useEntitySchema } from '@/lib/schema'
import { booleanSearchParam } from '@/lib/searchParams'

/**
 * The two dashboard drill-throughs, and the sentence each of them puts on screen.
 *
 * Named here rather than inferred from the URL at the point of use so that a filter can
 * never be applied without also being *stated*: a list silently shorter than the one the
 * user asked for is the failure this whole slice calls a partial result. Criterion 2 is
 * the other half -- these are the same predicates the operational dashboard's counts are
 * built from, evaluated by the same code in `packages/core`, which is why they travel to
 * the server rather than being reimplemented over the fetched page.
 */
const DRILL_THROUGHS = [
  {
    key: 'fatturato_non_vinto',
    label: 'Fatturato ma non vinto',
    explanation:
      'Solo i deal con almeno una fattura emessa che non risultano vinti. Togli il filtro per vedere tutti i deal.',
  },
  {
    key: 'da_fatturare',
    label: 'Vinto ma da fatturare',
    explanation:
      'Solo i deal vinti con ore fatturabili non ancora fatturate. Togli il filtro per vedere tutti i deal.',
  },
] as const

/** The same two, in the shape `FilterChips` takes. Derived rather than written twice, so
 *  a drill-through added above appears as a chip with no second edit -- and the chip's
 *  label stays the sentence the explanation below the row uses. */
const DRILL_THROUGH_OPTIONS = DRILL_THROUGHS.map((filter) => ({
  value: filter.key,
  label: filter.label,
}))

/**
 * Exported so `list.test.tsx` can render the list without a router; the route
 * component below is what reads the URL. Same split as `CustomersPage`/`PeoplePage`.
 */
export function DealsList({
  initialSearch,
  filters,
}: {
  initialSearch: string
  filters: Pick<DealsListParams, 'fatturato_non_vinto' | 'da_fatturare'>
}) {
  const navigate = useNavigate()
  const [search, setSearch] = useState(initialSearch)
  const schema = useEntitySchema('deal')
  const deals = useDeals({ search: search || undefined, ...filters })
  const active = DRILL_THROUGHS.filter((candidate) => filters[candidate.key] === true)

  // Recomputed every render, not memoised -- same call as `CustomersPage`/
  // `PeoplePage`'s identical line: not worth a `useMemo` whose dependency array
  // would just repeat the schema query's own result.
  const columns = buildDealColumns(schema.data?.custom_fields ?? [])

  return (
    <>
      <PageHeader
        icon={Handshake}
        title="Deal"
        description="La stessa pipeline della board, letta come elenco."
        actions={
          <Button variant="outline" asChild>
            <Link to="/app/deal">
              <LayoutGrid className="mr-2 size-4" />
              Vista Kanban
            </Link>
          </Button>
        }
      >
        <FilterRow>
          {/* The two chips are the two drill-throughs, and nothing more: this list has
              never had a stage filter and the revision does not add one. `FilterChips`
              rather than a hand-rolled row, so «Tutti» means the same thing here as it
              does on Fatture -- its `onChange` hands back `null` both for «Tutti» and for
              the pressed chip being pressed again, and this page's only job is to turn
              that into a URL.

              A URL and not local state, because the predicate is evaluated on the server
              (see `DRILL_THROUGHS` above and criterion 2) -- and `search` is an *updater*
              and not a literal object, which is the part that took a defect to learn: a
              literal replaces the whole query string, dropping the `?search=` term the
              command palette arrives with, and `DealsListRoute` keys the component on
              that term, so the list remounted with an empty search box. One at a time,
              too: the two predicates are disjoint by construction (a deal cannot be both
              not-won and won), so carrying the other one along would only ever produce an
              empty list. */}
          <FilterChips
            label="Filtra i deal"
            allLabel="Tutti"
            options={DRILL_THROUGH_OPTIONS}
            value={active[0]?.key ?? null}
            onChange={(next) =>
              void navigate({
                to: '/app/deal/list',
                search: (previous) => ({
                  search: previous.search,
                  ...(next === null ? {} : { [next]: true }),
                }),
              })
            }
          />

          <div className="relative w-full max-w-sm">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pl-9"
              placeholder="Cerca per nome…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
        </FilterRow>
      </PageHeader>

      <div className="px-8 pb-8">
        {/* The chip says which filter is on; this says what it is hiding. A filter
            applied without also being stated is what this slice calls a partial
            result. */}
        {active.length > 0 && (
          <div className="mb-4 space-y-2">
            {active.map((filter) => (
              <p
                key={filter.key}
                role="status"
                className="flex flex-wrap items-center gap-2 border bg-card px-3 py-2 text-sm"
              >
                <span>
                  <strong>{filter.label}</strong> — {filter.explanation}
                </span>
              </p>
            ))}
          </div>
        )}

        {deals.data?.truncated && (
          <p
            role="status"
            className="mb-4 border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
          >
            Ci sono troppi deal da mostrare tutti insieme: alcuni potrebbero mancare da questo
            elenco. Contatta un amministratore.
          </p>
        )}

        <DataTable
          columns={columns}
          data={deals.data?.items ?? []}
          isLoading={deals.isLoading}
          isError={deals.isError}
          error={deals.error}
          onRowClick={(row) =>
            void navigate({ to: '/app/deal/$dealId', params: { dealId: row.id } })
          }
          emptyMessage="Nessun deal."
        />
      </div>
    </>
  )
}

/**
 * The `?search=` term is an entry point, not a live mirror of the box. The palette's
 * «vedi tutti» links here carrying the term the user searched for, and `key` makes
 * arriving with a *different* term a remount, so the filter is seeded even when this
 * route is already open. Typing afterwards stays local: pushing every keystroke through
 * the router would make a controlled input wait on a navigation to echo the character
 * back, which is how a fast typist loses characters.
 */
function DealsListRoute() {
  const { search, fatturato_non_vinto, da_fatturare } = Route.useSearch()
  return (
    <DealsList
      key={search ?? ''}
      initialSearch={search ?? ''}
      filters={{ fatturato_non_vinto, da_fatturare }}
    />
  )
}

export const Route = createFileRoute('/app/deal/list')({
  component: DealsListRoute,
  // Declared so the palette can link here with a term, and so the operational dashboard
  // can link here with a drill-through (`<Link to={...} search={...} />` is typed against
  // this function's *return* type, not its parameter type). An empty or non-string value
  // is dropped rather than carried as `?search=`, so the URL never claims a filter that is
  // not applied -- and the two booleans are carried only when they are literally `true`,
  // so `?da_fatturare=false` is the absence of a filter rather than a third state.
  validateSearch: (
    search: Record<string, unknown>,
  ): { search?: string; fatturato_non_vinto?: boolean; da_fatturare?: boolean } => ({
    search:
      typeof search.search === 'string' && search.search.length > 0 ? search.search : undefined,
    fatturato_non_vinto: booleanSearchParam(search.fatturato_non_vinto),
    da_fatturare: booleanSearchParam(search.da_fatturare),
  }),
})
