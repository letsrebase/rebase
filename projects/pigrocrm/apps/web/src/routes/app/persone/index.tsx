import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { Plus, Search, Users } from 'lucide-react'
import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { DataTable } from '@/components/DataTable'
import { FilterRow } from '@/components/FilterRow'
import { PageHeader } from '@/components/PageHeader'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { useCustomers } from '@/features/customers/queries'
import { buildPersonColumns } from '@/features/people/columns'
import { PersonForm } from '@/features/people/PersonForm'
import { useCreatePerson, usePeople } from '@/features/people/queries'
import { toProblem, type ProblemDetail } from '@/lib/api'
import { useCanWrite } from '@/lib/auth'
import { useEntitySchema } from '@/lib/schema'

/** `tutte` is a UI-only value, the same idiom `fatture/index.tsx`'s `ANY` uses for its
 *  type select: the API filter is simply absent when nothing is chosen, and a `Select`
 *  needs a non-empty string to represent "no filter". A customer id is always a UUID
 *  minted by the database, so no real id can ever collide with this literal. */
const ALL_COMPANIES = 'tutte'

/**
 * The shape `GET /api/people`'s `customer_id` accepts
 * (`apps/api/src/pigrocrm_api/routers/people.py` types it `UUID | None`), checked here
 * before the value ever reaches a request. A stale bookmark, a typo or a hand-edited
 * query string carrying anything else must be dropped exactly like an empty `search`
 * is dropped below -- not sent to the API, where it would 422 and, through `unwrap()`,
 * turn the whole list into `DataTable`'s error banner over one bad URL parameter.
 */
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/**
 * Exported so `index.test.tsx` can render the list without a router -- the same split
 * `CustomersPage` makes next door.
 *
 * `customerId` is a controlled prop, not local state: unlike the search box, the
 * «Azienda» select has to navigate (see the `Select` below), because `customer_id` is
 * a real server-side filter (`GET /api/people?customer_id=`) and the URL is what
 * survives a bookmark, a reload or the `key`-driven remount `PeopleRoute` does for
 * `search`.
 */
export function PeoplePage({
  initialSearch,
  customerId,
}: {
  initialSearch: string
  customerId?: string
}) {
  const navigate = useNavigate()
  const canWrite = useCanWrite()
  const [search, setSearch] = useState(initialSearch)
  const [open, setOpen] = useState(false)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)

  const schema = useEntitySchema('person')
  const people = usePeople({ search: search || undefined, customer_id: customerId })
  const create = useCreatePerson()

  // `limit: 200` mirrors `PersonForm`'s own `CustomerPicker` -- a one-shot cap for a
  // dropdown, not this screen's own list.
  const customers = useCustomers({ limit: 200 })

  // Sorted here, client-side, rather than asked of the API: `useCustomers` has no
  // `sort` parameter of its own (its default order is `created_at`, see
  // `CustomersListParams`), and this dropdown is a handful of rows, not a page that
  // needs the server's keyset pagination to sort correctly.
  const sortedCustomers = useMemo(
    () =>
      [...(customers.data?.items ?? [])].sort((a, b) =>
        a.ragione_sociale.localeCompare(b.ragione_sociale, 'it'),
      ),
    [customers.data],
  )

  /**
   * What the trigger shows, computed rather than left to Radix's automatic
   * "match the selected `SelectItem`'s text" behaviour: that lookup only ever finds
   * a match once the matching item has actually rendered, and while `customers` is
   * loading -- or has failed -- `sortedCustomers` is empty, so a `customerId` from
   * the URL has no `SelectItem` yet. Radix's own fallback for that case is blank,
   * not the `placeholder`, which is reserved for a genuinely empty value -- so a
   * real, valid company id in the URL rendered a blank trigger while its name was in
   * flight, permanently if the request errored. Passing this as `SelectValue`'s
   * children overrides its automatic lookup entirely (Radix's own escape hatch for
   * "I decide what is shown"), and it never depends on `sortedCustomers` having
   * rendered anything.
   *
   * `customerId === undefined` is checked first and unconditionally: "no company
   * chosen" is a static fact about the URL, never something `/api/customers`
   * loading or failing should override.
   */
  const companyLabel = (): string => {
    if (customerId === undefined) return 'Tutte le aziende'
    if (customers.isLoading) return 'Caricamento aziende…'
    if (customers.isError) return 'Azienda non disponibile'
    return (
      sortedCustomers.find((customer) => customer.id === customerId)?.ragione_sociale ??
      'Azienda non disponibile'
    )
  }

  // Recomputed every render, not memoised -- same call as `CustomersPage`'s
  // identical line: `schema.data?.custom_fields` only changes when the schema
  // query itself refetches, and building a handful of plain objects is not
  // worth a `useMemo` whose dependency array would just repeat the query result.
  const columns = buildPersonColumns(schema.data?.custom_fields ?? [])

  return (
    <>
      <PageHeader
        icon={Users}
        title="Persone"
        actions={
          canWrite && (
            <Button
              onClick={() => {
                setProblem(null)
                setOpen(true)
              }}
            >
              <Plus className="mr-2 size-4" />
              Nuova persona
            </Button>
          )
        }
      >
        <FilterRow>
          <div className="relative w-full max-w-sm">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pl-9"
              placeholder="Cerca per nome, cognome o email…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>

          {/* Navigates rather than filtering locally, the same reasoning
              `deal/lista.tsx`'s chips carry for their own server-evaluated filter:
              `customer_id` is a real `GET /api/people` query parameter, and `search` is
              an *updater*, not a literal object, so the box's own `?search=` term
              survives a company chosen here. */}
          <Select
            value={customerId ?? ALL_COMPANIES}
            onValueChange={(next) =>
              void navigate({
                to: '/app/persone',
                search: (previous) => ({
                  search: previous.search,
                  customer_id: next === ALL_COMPANIES ? undefined : next,
                }),
              })
            }
          >
            {/* Disabled, not hidden, while the company list is loading or failed to
                load: «Tutte le aziende» is still a real choice either way, and a
                disabled trigger (the existing `disabled:opacity-50` on
                `SelectTrigger`, no new colour) is the cue that the list behind it may
                be incomplete -- distinct from a tenant that genuinely has no
                customers. */}
            <SelectTrigger
              className="w-56"
              aria-label="Filtra per azienda"
              disabled={customers.isLoading || customers.isError}
            >
              <SelectValue>{companyLabel()}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_COMPANIES}>Tutte le aziende</SelectItem>
              {sortedCustomers.map((customer) => (
                <SelectItem key={customer.id} value={customer.id}>
                  {customer.ragione_sociale}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </FilterRow>
      </PageHeader>

      <div className="px-8 pb-8">
        <DataTable
          columns={columns}
          data={people.data?.items ?? []}
          isLoading={people.isLoading}
          isError={people.isError}
          error={people.error}
          onRowClick={(row) =>
            void navigate({ to: '/app/persone/$personId', params: { personId: row.id } })
          }
          emptyMessage="Nessuna persona. Creane una per iniziare."
        />
      </div>

      <PersonForm
        title="Nuova persona"
        open={open}
        onOpenChange={setOpen}
        customFields={schema.data?.custom_fields ?? []}
        problem={problem}
        busy={create.isPending}
        onSubmit={(values) => {
          setProblem(null)
          create.mutate(values, {
            onSuccess: () => {
              setOpen(false)
              toast.success('Persona creata')
            },
            onError: (error) => setProblem(toProblem(error)),
          })
        }}
      />
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
 *
 * `customer_id` is not part of that `key`: unlike `search`, the «Azienda» select is a
 * controlled prop of `PeoplePage` (see its own docstring), so a change to it re-renders
 * the page instead of remounting it.
 */
function PeopleRoute() {
  const { search, customer_id } = Route.useSearch()
  return <PeoplePage key={search ?? ''} initialSearch={search ?? ''} customerId={customer_id} />
}

export const Route = createFileRoute('/app/persone/')({
  component: PeopleRoute,
  // Declared so the palette can link here with a term (`navigate({ to, search })` is
  // typed against this). An empty or non-string value is dropped rather than carried as
  // `?search=`/`?customer_id=`, so the URL never claims a filter that is not applied.
  validateSearch: (
    search: Record<string, unknown>,
  ): { search?: string; customer_id?: string } => ({
    search:
      typeof search.search === 'string' && search.search.length > 0 ? search.search : undefined,
    customer_id:
      typeof search.customer_id === 'string' && UUID_RE.test(search.customer_id)
        ? search.customer_id
        : undefined,
  }),
})
