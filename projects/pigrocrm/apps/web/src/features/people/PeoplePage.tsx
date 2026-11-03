import { useNavigate } from '@tanstack/react-router'
import { Plus, Search, Users } from 'lucide-react'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { DataTable } from '@/components/DataTable'
import { FilterRow } from '@/components/FilterRow'
import { LoadMore } from '@/components/LoadMore'
import { PageHeader } from '@/components/PageHeader'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@rebase/ui/popover'
import { CustomerPicker } from '@/features/customers/CustomerPicker'
import { useCustomer } from '@/features/customers/queries'
import { buildPersonColumns } from '@/features/people/columns'
import { PersonForm } from '@/features/people/PersonForm'
import { useCreatePerson, usePeoplePages } from '@/features/people/queries'
import { SEARCH_DEBOUNCE_MS } from '@/features/search/queries'
import { useDebounced } from '@/hooks/useDebounced'
import { toProblem, type ProblemDetail } from '@/lib/api'
import { useCanWrite } from '@/lib/auth'
import { useEntitySchema } from '@/lib/schema'

/**
 * The «Azienda» filter: a button that says which company the list is narrowed to, and
 * opens the customer search to choose another one or to take the filter off.
 *
 * A search and not a dropdown of every customer: the list of companies is as long as the
 * space's customer list, and a `Select` of the first 200 silently stopped at the 200th.
 * The label is read from the chosen id (`useCustomer`) rather than from a list, so a
 * company from the URL is named whether or not it is among any page of matches. A
 * company that cannot be read says so instead of leaving the trigger blank.
 */
function CompanyFilter({
  customerId,
  onChange,
}: {
  customerId: string | undefined
  onChange: (customerId: string | undefined) => void
}) {
  const [open, setOpen] = useState(false)
  const company = useCustomer(customerId ?? '')
  const label =
    customerId === undefined
      ? 'Tutte le aziende'
      : company.isError
        ? 'Azienda non disponibile'
        : (company.data?.legal_name ?? 'Caricamento…')
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" aria-label="Filtra per azienda" className="w-56 justify-start">
          <span className="truncate">{label}</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-96">
        <CustomerPicker
          id="filtro-azienda"
          value={customerId ?? null}
          ariaLabel="Cerca un'azienda"
          placeholder="Tutte le aziende: cerca per filtrare…"
          onChange={(customer) => {
            setOpen(false)
            onChange(customer?.id)
          }}
        />
      </PopoverContent>
    </Popover>
  )
}

/**
 * Exported so `index.test.tsx` can render the list without a router -- the same split
 * `CustomersPage` makes next door.
 *
 * `customerId` is a controlled prop, not local state: unlike the search box, the
 * «Azienda» filter has to navigate (see `CompanyFilter` above), because `customer_id` is
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
  // The request follows the settled term, not every keystroke.
  const settled = useDebounced(search, SEARCH_DEBOUNCE_MS)
  const people = usePeoplePages({ search: settled || undefined, customer_id: customerId })
  const create = useCreatePerson()

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
              `deal/list.tsx`'s chips carry for their own server-evaluated filter:
              `customer_id` is a real `GET /api/people` query parameter, and `search` is
              an *updater*, not a literal object, so the box's own `?search=` term
              survives a company chosen here. */}
          <CompanyFilter
            customerId={customerId}
            onChange={(next) =>
              void navigate({
                to: '/app/people',
                search: (previous) => ({ search: previous.search, customer_id: next }),
              })
            }
          />
        </FilterRow>
      </PageHeader>

      <div className="px-8 pb-8">
        <DataTable
          label="Persone"
          columns={columns}
          data={people.items}
          isLoading={people.isLoading}
          isError={people.isError}
          error={people.error}
          onRowClick={(row) =>
            void navigate({ to: '/app/people/$personId', params: { personId: row.id } })
          }
          emptyMessage="Nessuna persona. Creane una per iniziare."
        />
        {people.hasMore && (
          <LoadMore
            label="Carica altre"
            message={`Mostrate ${people.items.length} persone, ce ne sono altre.`}
            isFetchingMore={people.isFetchingMore}
            hasError={people.isError}
            error={people.error}
            onLoadMore={people.loadMore}
          />
        )}
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
