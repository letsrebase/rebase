import { useNavigate, useSearch } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { Input } from '@rebase/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Table, TableBody, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin, yearsFilter, type Remote, type TalentsFilters } from '@/lib/api'
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
import { FREELANCER_LIST_STATES, REMOTE_LABELS, SENIORITY_LABELS } from '@/lib/format'
import { keys } from '@/lib/query-keys'
import { DuplicatesLink } from '../DuplicateMarks'
import { AmountFilter, Empty, FilterField, Header, LoadMore, StateFilter } from '../list-kit'
import { TalentRow } from './TalentRow'

/** What `sort` can be, in the words the screen shows (the keys are the service's own). */
const SORT_LABELS: Record<string, string> = {
  rate_asc: 'Tariffa, dalla più bassa',
  rate_desc: 'Tariffa, dalla più alta',
  experience_years_asc: 'Anni di esperienza, dal minore',
  experience_years_desc: 'Anni di esperienza, dal maggiore',
}

/** The state of a talent's anonymous card, as the `card` filter names it. */
const CARD_LABELS: Record<string, string> = {
  present: 'Presente',
  failed: 'Non riuscita',
  absent: 'Assente',
}

/** «Talenti»: every freelancer card and every bare sign-up as one list (REB-282/283),
 *  `status` `lead` for the bare ones and the freelancer's own state otherwise -- the
 *  single list that replaced «Developer e CTO» and «Iscrizioni». A card row opens the
 *  existing freelancer detail; a lead row opens the page that offers to draft one.
 *  REB-286 adds the search box, the filter row and infinite scroll, all three carried
 *  in the URL through `validateSearch` on `/admin/talent` (`router.tsx`) so a reload
 *  or a shared link reproduces the exact view; only `q` is debounced client-side
 *  before it reaches the URL and the query, everything else applies immediately like
 *  the state pills always have. */
export function AdminTalents() {
  const search = useSearch({ from: '/signedIn/admin/talent' })
  const navigate = useNavigate()
  const [qInput, setQInput] = useState(search.q ?? '')
  const debouncedQ = useDebounce(qInput, SEARCH_DEBOUNCE_MS)

  useEffect(() => {
    if (debouncedQ === (search.q ?? '')) return
    void navigate({
      to: '/admin/talent',
      search: (prev: TalentsFilters) => ({ ...prev, q: debouncedQ || undefined }),
      replace: true,
    })
  }, [debouncedQ, navigate, search.q])

  function setFilter<K extends keyof TalentsFilters>(key: K, value: TalentsFilters[K]) {
    void navigate({
      to: '/admin/talent',
      search: (prev: TalentsFilters) => ({ ...prev, [key]: value }),
      replace: true,
    })
  }

  const filters: TalentsFilters = {
    ...search,
    q: debouncedQ || undefined,
    min_rate: amountFilter(search.min_rate),
    max_rate: amountFilter(search.max_rate),
    min_years: yearsFilter(search.min_years)?.toString(),
    max_years: yearsFilter(search.max_years)?.toString(),
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
      <Header title="Talenti" count={list.data?.pages[0]?.total}>
        <div className="flex flex-wrap items-center gap-3">
          <Select value={search.sort ?? ANY} onValueChange={(value) => setFilter('sort', value === ANY ? undefined : value)}>
            <SelectTrigger id="talents-sort" aria-label="Ordina per" className="w-56">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Più recenti</SelectItem>
              {Object.entries(SORT_LABELS).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <DuplicatesLink />
          <StateFilter
            states={FREELANCER_LIST_STATES}
            value={search.status}
            onChange={(value) => setFilter('status', value)}
          />
        </div>
      </Header>
      <div className="grid gap-4 border-b px-6 py-4 sm:grid-cols-2 lg:grid-cols-4">
        <FilterField label="Cerca" htmlFor="talents-q">
          <Input
            id="talents-q"
            type="search"
            maxLength={200}
            placeholder="Nome, cognome, email, posizione…"
            value={qInput}
            onChange={(event) => setQInput(event.target.value)}
          />
        </FilterField>
        <FilterField label="Competenze (separate da virgole)" htmlFor="talents-skills">
          <Input
            id="talents-skills"
            placeholder="LLM, Python…"
            value={search.skills ?? ''}
            onChange={(event) => setFilter('skills', event.target.value || undefined)}
          />
        </FilterField>
        <FilterField label="Seniority" htmlFor="talents-seniority">
          <Select
            value={search.seniority ?? ANY}
            onValueChange={(value) => setFilter('seniority', value === ANY ? undefined : value)}
          >
            <SelectTrigger id="talents-seniority">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Tutte</SelectItem>
              {Object.entries(SENIORITY_LABELS).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </FilterField>
        <FilterField label="Anni di esperienza, da" htmlFor="talents-min-years">
          <Input
            id="talents-min-years"
            type="number"
            min={0}
            inputMode="numeric"
            value={search.min_years ?? ''}
            onChange={(event) => setFilter('min_years', event.target.value || undefined)}
          />
        </FilterField>
        <FilterField label="Anni di esperienza, fino a" htmlFor="talents-max-years">
          <Input
            id="talents-max-years"
            type="number"
            min={0}
            inputMode="numeric"
            value={search.max_years ?? ''}
            onChange={(event) => setFilter('max_years', event.target.value || undefined)}
          />
        </FilterField>
        <FilterField label="Luogo" htmlFor="talents-location">
          <Input
            id="talents-location"
            placeholder="Milano…"
            value={search.location ?? ''}
            onChange={(event) => setFilter('location', event.target.value || undefined)}
          />
        </FilterField>
        <FilterField label="Settori (separati da virgole)" htmlFor="talents-industries">
          <Input
            id="talents-industries"
            placeholder="fintech…"
            value={search.industries ?? ''}
            onChange={(event) => setFilter('industries', event.target.value || undefined)}
          />
        </FilterField>
        <FilterField label="Lingue (separate da virgole)" htmlFor="talents-languages">
          <Input
            id="talents-languages"
            placeholder="inglese…"
            value={search.languages ?? ''}
            onChange={(event) => setFilter('languages', event.target.value || undefined)}
          />
        </FilterField>
        <FilterField label="Scheda anonima" htmlFor="talents-card">
          <Select value={search.card ?? ANY} onValueChange={(value) => setFilter('card', value === ANY ? undefined : value)}>
            <SelectTrigger id="talents-card">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Tutte</SelectItem>
              {Object.entries(CARD_LABELS).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </FilterField>
        <FilterField label="Posizione" htmlFor="talents-job-title">
          <Input
            id="talents-job-title"
            value={search.job_title ?? ''}
            onChange={(event) => setFilter('job_title', event.target.value || undefined)}
          />
        </FilterField>
        <FilterField label="Da remoto" htmlFor="talents-remote">
          <Select
            value={search.remote ?? ANY}
            onValueChange={(value) => setFilter('remote', value === ANY ? undefined : (value as Remote))}
          >
            <SelectTrigger id="talents-remote">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Tutti</SelectItem>
              {Object.entries(REMOTE_LABELS).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </FilterField>
        <AmountFilter
          id="talents-min-rate"
          label="Tariffa min (€/giorno)"
          value={search.min_rate}
          onChange={(value) => setFilter('min_rate', value)}
        />
        <AmountFilter
          id="talents-max-rate"
          label="Tariffa max (€/giorno)"
          value={search.max_rate}
          onChange={(value) => setFilter('max_rate', value)}
        />
        <FilterField label="Pagina di provenienza" htmlFor="talents-origin">
          <Input
            id="talents-origin"
            placeholder="home, pigrocrm…"
            value={search.origin_page ?? ''}
            onChange={(event) => setFilter('origin_page', event.target.value || undefined)}
          />
        </FilterField>
        <FilterField label="UTM source" htmlFor="talents-utm-source">
          <Input
            id="talents-utm-source"
            value={search.utm_source ?? ''}
            onChange={(event) => setFilter('utm_source', event.target.value || undefined)}
          />
        </FilterField>
        <FilterField label="Ha un CV" htmlFor="talents-has-cv">
          <Select value={boolToSelect(search.has_cv)} onValueChange={(value) => setFilter('has_cv', selectToBool(value))}>
            <SelectTrigger id="talents-has-cv">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Tutti</SelectItem>
              <SelectItem value="yes">Sì</SelectItem>
              <SelectItem value="no">No</SelectItem>
            </SelectContent>
          </Select>
        </FilterField>
        <FilterField label="Ha fatto accesso" htmlFor="talents-with-logins">
          <Select
            value={boolToSelect(search.with_logins)}
            onValueChange={(value) => setFilter('with_logins', selectToBool(value))}
          >
            <SelectTrigger id="talents-with-logins">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Tutti</SelectItem>
              <SelectItem value="yes">Sì</SelectItem>
              <SelectItem value="no">No</SelectItem>
            </SelectContent>
          </Select>
        </FilterField>
        <FilterField label="Creato dal" htmlFor="talents-created-from">
          <Input
            id="talents-created-from"
            type="date"
            value={search.created_from ?? ''}
            onChange={(event) => setFilter('created_from', event.target.value || undefined)}
          />
        </FilterField>
        <FilterField label="Creato al" htmlFor="talents-created-to">
          <Input
            id="talents-created-to"
            type="date"
            value={search.created_to ?? ''}
            onChange={(event) => setFilter('created_to', event.target.value || undefined)}
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
                    <TableHead>Profilo</TableHead>
                    <TableHead>Competenze</TableHead>
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
                    <TalentRow key={item.id} item={item} />
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
