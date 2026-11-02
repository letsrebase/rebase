import { useNavigate, useSearch } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { Input } from '@rebase/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Table, TableBody, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin, type Remoto, type TalentiFilters } from '@/lib/api'
import {
  ANY,
  SEARCH_DEBOUNCE_MS,
  boolToSelect,
  isFilterActive,
  pageItems,
  selectToBool,
  useCursorList,
  useDebounce,
} from '@/lib/adminList'
import { amountFilter } from '@/lib/amount'
import { FREELANCER_LIST_STATES, REMOTO_LABELS } from '@/lib/format'
import { keys } from '@/lib/query-keys'
import { DuplicatesLink } from '../DuplicateMarks'
import { AmountFilter, Empty, FilterField, Header, LoadMore, StateFilter } from '../list-kit'
import { TalentoRow } from './TalentRow'

/** «Talenti»: every freelancer card and every bare sign-up as one list (REB-282/283),
 *  `stato` `lead` for the bare ones and the freelancer's own state otherwise -- the
 *  single list that replaced «Developer e CTO» and «Iscrizioni». A card row opens the
 *  existing freelancer detail; a lead row opens the page that offers to draft one.
 *  REB-286 adds the search box, the filter row and infinite scroll, all three carried
 *  in the URL through `validateSearch` on `/admin/talent` (`router.tsx`) so a reload
 *  or a shared link reproduces the exact view; only `q` is debounced client-side
 *  before it reaches the URL and the query, everything else applies immediately like
 *  the state pills always have. */
export function AdminTalenti() {
  const search = useSearch({ from: '/signedIn/admin/talent' })
  const navigate = useNavigate()
  const [qInput, setQInput] = useState(search.q ?? '')
  const debouncedQ = useDebounce(qInput, SEARCH_DEBOUNCE_MS)

  useEffect(() => {
    if (debouncedQ === (search.q ?? '')) return
    void navigate({
      to: '/admin/talent',
      search: (prev: TalentiFilters) => ({ ...prev, q: debouncedQ || undefined }),
      replace: true,
    })
  }, [debouncedQ, navigate, search.q])

  function setFilter<K extends keyof TalentiFilters>(key: K, value: TalentiFilters[K]) {
    void navigate({
      to: '/admin/talent',
      search: (prev: TalentiFilters) => ({ ...prev, [key]: value }),
      replace: true,
    })
  }

  const filters: TalentiFilters = {
    ...search,
    q: debouncedQ || undefined,
    tariffa_min: amountFilter(search.tariffa_min),
    tariffa_max: amountFilter(search.tariffa_max),
  }
  const { list, items } = useCursorList({
    queryKey: keys.talents(filters),
    fetchPage: (cursor) => admin.talent({ ...filters, cursor }),
    itemsOf: pageItems,
  })
  // What the request carries: an amount it cannot send narrows nothing, so it is no filter.
  const activeFilters = isFilterActive(filters)

  return (
    <>
      <Header title="Talenti" count={list.data?.pages[0]?.totale}>
        <div className="flex flex-wrap items-center gap-3">
          <DuplicatesLink />
          <StateFilter
            states={FREELANCER_LIST_STATES}
            value={search.stato}
            onChange={(value) => setFilter('stato', value)}
          />
        </div>
      </Header>
      <div className="grid gap-4 border-b px-6 py-4 sm:grid-cols-2 lg:grid-cols-4">
        <FilterField label="Cerca" htmlFor="talenti-q">
          <Input
            id="talenti-q"
            type="search"
            maxLength={200}
            placeholder="Nome, cognome, email, posizione…"
            value={qInput}
            onChange={(event) => setQInput(event.target.value)}
          />
        </FilterField>
        <FilterField label="Posizione" htmlFor="talenti-posizione">
          <Input
            id="talenti-posizione"
            value={search.posizione ?? ''}
            onChange={(event) => setFilter('posizione', event.target.value || undefined)}
          />
        </FilterField>
        <FilterField label="Da remoto" htmlFor="talenti-remoto">
          <Select
            value={search.remoto ?? ANY}
            onValueChange={(value) => setFilter('remoto', value === ANY ? undefined : (value as Remoto))}
          >
            <SelectTrigger id="talenti-remoto">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Tutti</SelectItem>
              {Object.entries(REMOTO_LABELS).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </FilterField>
        <AmountFilter
          id="talenti-tariffa-min"
          label="Tariffa min (€/giorno)"
          value={search.tariffa_min}
          onChange={(value) => setFilter('tariffa_min', value)}
        />
        <AmountFilter
          id="talenti-tariffa-max"
          label="Tariffa max (€/giorno)"
          value={search.tariffa_max}
          onChange={(value) => setFilter('tariffa_max', value)}
        />
        <FilterField label="Pagina di provenienza" htmlFor="talenti-origine">
          <Input
            id="talenti-origine"
            placeholder="home, pigrocrm…"
            value={search.pagina_origine ?? ''}
            onChange={(event) => setFilter('pagina_origine', event.target.value || undefined)}
          />
        </FilterField>
        <FilterField label="UTM source" htmlFor="talenti-utm-source">
          <Input
            id="talenti-utm-source"
            value={search.utm_source ?? ''}
            onChange={(event) => setFilter('utm_source', event.target.value || undefined)}
          />
        </FilterField>
        <FilterField label="Ha un CV" htmlFor="talenti-has-cv">
          <Select value={boolToSelect(search.has_cv)} onValueChange={(value) => setFilter('has_cv', selectToBool(value))}>
            <SelectTrigger id="talenti-has-cv">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Tutti</SelectItem>
              <SelectItem value="si">Sì</SelectItem>
              <SelectItem value="no">No</SelectItem>
            </SelectContent>
          </Select>
        </FilterField>
        <FilterField label="Ha fatto accesso" htmlFor="talenti-con-accessi">
          <Select
            value={boolToSelect(search.con_accessi)}
            onValueChange={(value) => setFilter('con_accessi', selectToBool(value))}
          >
            <SelectTrigger id="talenti-con-accessi">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Tutti</SelectItem>
              <SelectItem value="si">Sì</SelectItem>
              <SelectItem value="no">No</SelectItem>
            </SelectContent>
          </Select>
        </FilterField>
        <FilterField label="Creato dal" htmlFor="talenti-creato-da">
          <Input
            id="talenti-creato-da"
            type="date"
            value={search.creato_da ?? ''}
            onChange={(event) => setFilter('creato_da', event.target.value || undefined)}
          />
        </FilterField>
        <FilterField label="Creato al" htmlFor="talenti-creato-a">
          <Input
            id="talenti-creato-a"
            type="date"
            value={search.creato_a ?? ''}
            onChange={(event) => setFilter('creato_a', event.target.value || undefined)}
          />
        </FilterField>
      </div>
      {list.isError ? (
        <Empty>Non riesco a leggere la lista.</Empty>
      ) : list.isPending ? (
        <Empty>Caricamento…</Empty>
      ) : items.length === 0 ? (
        <Empty>{activeFilters ? 'Nessun risultato per questi filtri.' : 'Nessun profilo qui.'}</Empty>
      ) : (
        <>
          {/* The record's ruled table, the shape #228 gave the CRM's lists: a white
             container closed by a 1px ink line, 48px rows, a 2px rule under the header
             and a 1px separator between the cells, all from `@rebase/ui/table`. The
             padding the raw `th`/`td` used to type is the primitive's own. */}
          <div className="px-6 pt-6 pb-6">
            <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
              <Table scrollLabel="Talenti">
                <TableHeader>
                  <TableRow>
                    <TableHead>Chi</TableHead>
                    <TableHead>Stato</TableHead>
                    <TableHead className="text-right">Tariffa</TableHead>
                    <TableHead>Modalità</TableHead>
                    <TableHead>LinkedIn</TableHead>
                    <TableHead>Provenienza</TableHead>
                    <TableHead className="text-right">Quando</TableHead>
                    <TableHead className="w-12"><span className="sr-only">Azioni</span></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((item) => (
                    <TalentoRow key={item.id} item={item} />
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
          {list.hasNextPage && (
            <LoadMore
              label={`Mostrati ${items.length} talenti, ce ne sono altri.`}
              isFetchingMore={list.isFetchingNextPage}
              onLoadMore={() => void list.fetchNextPage()}
            />
          )}
        </>
      )}
    </>
  )
}
