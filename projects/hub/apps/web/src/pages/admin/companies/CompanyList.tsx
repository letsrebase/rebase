import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin, type CompaniesFilters } from '@/lib/api'
import { SEARCH_DEBOUNCE_MS, isFilterActive, pageItems, useCursorList, useDebounce } from '@/lib/adminList'
import { amountFilter } from '@/lib/amount'
import { COMPANY_STATES, TEAM_BUILDER_ORIGIN, formatDate } from '@/lib/format'
import { formatMoney } from '@rebase/ui/format'
import { keys } from '@/lib/query-keys'
import { AmountFilter, Empty, FilterField, Header, LoadingState, LoadMore, StateFilter, StatePill, TeamBuilderPill } from '../list-kit'

/** «Aziende»: every company request as a list. REB-286 adds the search box, the
 *  filter row and infinite scroll, all three carried in the URL through
 *  `validateSearch` on `/admin/companies` (`router.tsx`) the same way `AdminTalents`
 *  carries its own -- see that component's own note for why only `q` is debounced. */
export function AdminCompanies() {
  const search = useSearch({ from: '/signedIn/admin/companies' })
  const navigate = useNavigate()
  const [qInput, setQInput] = useState(search.q ?? '')
  const debouncedQ = useDebounce(qInput, SEARCH_DEBOUNCE_MS)

  useEffect(() => {
    if (debouncedQ === (search.q ?? '')) return
    void navigate({
      to: '/admin/companies',
      search: (prev: CompaniesFilters) => ({ ...prev, q: debouncedQ || undefined }),
      replace: true,
    })
  }, [debouncedQ, navigate, search.q])

  function setFilter<K extends keyof CompaniesFilters>(key: K, value: CompaniesFilters[K]) {
    void navigate({
      to: '/admin/companies',
      search: (prev: CompaniesFilters) => ({ ...prev, [key]: value }),
      replace: true,
    })
  }

  const filters: CompaniesFilters = {
    ...search,
    q: debouncedQ || undefined,
    budget_min: amountFilter(search.budget_min),
    budget_max: amountFilter(search.budget_max),
  }
  const { list, items } = useCursorList({
    queryKey: keys.companies(filters),
    fetchPage: (cursor) => admin.companies({ ...filters, cursor }),
    itemsOf: pageItems,
  })
  // What the request carries: an amount it cannot send narrows nothing, so it is no filter.
  const activeFilters = isFilterActive(filters)

  return (
    <>
      <Header title="Aziende" count={list.data?.pages[0]?.total}>
        <div className="flex flex-wrap items-center gap-3">
          <StateFilter states={COMPANY_STATES} value={search.status} onChange={(value) => setFilter('status', value)} />
          <Button asChild size="sm">
            <Link to="/admin/companies/new">Nuova richiesta</Link>
          </Button>
        </div>
      </Header>
      <div className="grid gap-4 border-b px-6 py-4 sm:grid-cols-2 lg:grid-cols-4">
        <FilterField label="Cerca" htmlFor="companies-q">
          <Input
            id="companies-q"
            type="search"
            maxLength={200}
            placeholder="Azienda, referente, email, progetto…"
            value={qInput}
            onChange={(event) => setQInput(event.target.value)}
          />
        </FilterField>
        <AmountFilter
          id="companies-budget-min"
          label="Budget min (€/giorno)"
          value={search.budget_min}
          onChange={(value) => setFilter('budget_min', value)}
        />
        <AmountFilter
          id="companies-budget-max"
          label="Budget max (€/giorno)"
          value={search.budget_max}
          onChange={(value) => setFilter('budget_max', value)}
        />
        <FilterField label="Periodo dal" htmlFor="companies-period-from">
          <Input
            id="companies-period-from"
            type="date"
            value={search.period_from ?? ''}
            onChange={(event) => setFilter('period_from', event.target.value || undefined)}
          />
        </FilterField>
        <FilterField label="Pagina di provenienza" htmlFor="companies-origin">
          <Input
            id="companies-origin"
            placeholder="home, pigrocrm…"
            value={search.origin ?? ''}
            onChange={(event) => setFilter('origin', event.target.value || undefined)}
          />
        </FilterField>
        <FilterField label="Creata dal" htmlFor="companies-created-from">
          <Input
            id="companies-created-from"
            type="date"
            value={search.created_from ?? ''}
            onChange={(event) => setFilter('created_from', event.target.value || undefined)}
          />
        </FilterField>
        <FilterField label="Creata al" htmlFor="companies-created-to">
          <Input
            id="companies-created-to"
            type="date"
            value={search.created_to ?? ''}
            onChange={(event) => setFilter('created_to', event.target.value || undefined)}
          />
        </FilterField>
      </div>
      {list.isError ? (
        <Empty>Non riesco a leggere la lista.</Empty>
      ) : list.isPending ? (
        <LoadingState />
      ) : items.length === 0 ? (
        <Empty>{activeFilters ? 'Nessun risultato per questi filtri.' : 'Nessuna richiesta qui.'}</Empty>
      ) : (
        <>
          <div className="px-6 pt-6 pb-6">
            <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
              <Table scrollLabel="Aziende">
                <TableHeader>
                  <TableRow>
                    <TableHead>Azienda</TableHead>
                    <TableHead>Progetto</TableHead>
                    <TableHead>Periodo</TableHead>
                    <TableHead className="text-right">Budget</TableHead>
                    <TableHead>Stato</TableHead>
                    <TableHead className="text-right">Quando</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((item) => (
                    <TableRow key={item.id}>
                      <TableCell>
                        <Link to="/admin/companies/$id" params={{ id: item.id }} className="font-medium hover:underline">
                          {item.company_name}
                        </Link>
                        {item.origin === TEAM_BUILDER_ORIGIN && (
                          <span className="ml-2">
                            <TeamBuilderPill />
                          </span>
                        )}
                        <p className="text-xs text-muted-foreground">{item.contact_person} · {item.email}</p>
                      </TableCell>
                      <TableCell className="max-w-xs truncate">{item.project}</TableCell>
                      <TableCell>dal {formatDate(item.period_from)}, {item.duration}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatMoney(item.daily_budget)}</TableCell>
                      <TableCell><StatePill status={item.status} /></TableCell>
                      <TableCell className="text-right text-muted-foreground">{formatDate(item.created_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
          {list.hasNextPage && (
            <LoadMore
              label={`Mostrate ${items.length} aziende, ce ne sono altre.`}
              isFetchingMore={list.isFetchingNextPage}
              onLoadMore={() => void list.fetchNextPage()}
            />
          )}
        </>
      )}
    </>
  )
}
