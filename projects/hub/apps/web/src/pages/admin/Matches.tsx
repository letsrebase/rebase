import { useInfiniteQuery } from '@tanstack/react-query'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { useEffect, useMemo, useState } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Input } from '@rebase/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin, type MatchesFilters, type MatchListItem } from '@/lib/api'
import { SEARCH_DEBOUNCE_MS, isFilterActive, useDebounce } from '@/lib/adminList'
import { DOCUMENT_STATE_LABELS, MATCH_STATES, MATCH_STATE_LABELS, formatDate } from '@/lib/format'
import { Empty, FilterField, Header, LoadMore, StateFilter } from './lists'

// The server's own default page size (`LIST_LIMIT_DEFAULT`, `rebase_core.matches`):
// the same mechanism «Talenti» and «Aziende» paginate with, a page at a time through
// «Mostra altri», just offset rather than cursor (REB-413's own brief).
const PAGE_SIZE = 100

function MatchRow({ item }: { item: MatchListItem }) {
  const name = `${item.freelancer_nome} ${item.freelancer_cognome}`.trim()
  return (
    <TableRow>
      <TableCell>
        <Link
          to="/admin/freelance/$id/contracts"
          params={{ id: item.freelancer_id }}
          className="font-medium hover:underline"
        >
          {name || item.freelancer_email}
        </Link>
        <p className="text-xs text-muted-foreground">{item.freelancer_email}</p>
      </TableCell>
      <TableCell>
        <p className="font-medium">{item.nome_azienda}</p>
        <p className="text-xs text-muted-foreground">{item.figura_richiesta}</p>
      </TableCell>
      <TableCell>
        <Badge variant="pill">{MATCH_STATE_LABELS[item.stato] ?? item.stato}</Badge>
      </TableCell>
      <TableCell>
        {item.lettera_numero && (
          <>
            <p className="text-sm">n. {item.lettera_numero}</p>
            {item.lettera_stato && (
              <p className="text-xs text-muted-foreground">
                {DOCUMENT_STATE_LABELS[item.lettera_stato] ?? item.lettera_stato}
              </p>
            )}
          </>
        )}
      </TableCell>
      <TableCell className="text-muted-foreground">
        {item.lettera_data_inizio}
        {item.lettera_data_fine && ` · ${item.lettera_data_fine}`}
      </TableCell>
      <TableCell className="text-right text-muted-foreground">
        <p>{formatDate(item.created_at)}</p>
        <p className="text-xs">{item.created_by_nome || item.created_by_email}</p>
      </TableCell>
    </TableRow>
  )
}

/** «Match» (REB-413): every match the admin area has, newest first -- the last
 *  addition to the milestone, over `MatchService.list_all` and the same filter/search/
 *  pagination mechanism «Talenti» and «Aziende» already use (`lists.tsx`), offset in
 *  place of a cursor since the list needs no keyset merge of two sources. */
export function AdminMatches() {
  const search = useSearch({ from: '/signedIn/admin/matches' })
  const navigate = useNavigate()
  const [qInput, setQInput] = useState(search.q ?? '')
  const debouncedQ = useDebounce(qInput, SEARCH_DEBOUNCE_MS)

  useEffect(() => {
    if (debouncedQ === (search.q ?? '')) return
    void navigate({
      to: '/admin/matches',
      search: (prev: MatchesFilters) => ({ ...prev, q: debouncedQ || undefined }),
      replace: true,
    })
  }, [debouncedQ, navigate, search.q])

  function setFilter<K extends keyof MatchesFilters>(key: K, value: MatchesFilters[K]) {
    void navigate({
      to: '/admin/matches',
      search: (prev: MatchesFilters) => ({ ...prev, [key]: value }),
      replace: true,
    })
  }

  const filters: MatchesFilters = { ...search, q: debouncedQ || undefined }
  const list = useInfiniteQuery({
    queryKey: ['matches', filters],
    queryFn: ({ pageParam }: { pageParam: number }) =>
      admin.matches({ ...filters, limit: PAGE_SIZE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, pages) => {
      const seen = pages.reduce((total, page) => total + page.items.length, 0)
      return seen < lastPage.totale ? seen : undefined
    },
  })
  const items = useMemo(() => list.data?.pages.flatMap((page) => page.items) ?? [], [list.data?.pages])
  const activeFilters = isFilterActive(search)

  return (
    <>
      <Header title="Match" count={list.data?.pages[0]?.totale}>
        <StateFilter
          states={MATCH_STATES}
          value={search.stato}
          onChange={(value) => setFilter('stato', value)}
          labels={MATCH_STATE_LABELS}
        />
      </Header>
      <div className="grid gap-4 border-b px-6 py-4 sm:grid-cols-2 lg:grid-cols-4">
        <FilterField label="Cerca" htmlFor="matches-q">
          <Input
            id="matches-q"
            type="search"
            maxLength={200}
            placeholder="Nome, cognome, email, azienda…"
            value={qInput}
            onChange={(event) => setQInput(event.target.value)}
          />
        </FilterField>
      </div>
      {list.isError ? (
        <Empty>Non riesco a leggere la lista.</Empty>
      ) : list.isPending ? (
        <Empty>Caricamento…</Empty>
      ) : items.length === 0 ? (
        <Empty>{activeFilters ? 'Nessun risultato per questi filtri.' : 'Nessun match qui.'}</Empty>
      ) : (
        <>
          <div className="px-6 pt-6 pb-6">
            <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Freelance</TableHead>
                    <TableHead>Azienda</TableHead>
                    <TableHead>Stato</TableHead>
                    <TableHead>Lettera</TableHead>
                    <TableHead>Periodo</TableHead>
                    <TableHead className="text-right">Creato</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((item) => (
                    <MatchRow key={item.id} item={item} />
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
          {list.hasNextPage && (
            <LoadMore
              label={`Mostrati ${items.length} match, ce ne sono altri.`}
              isFetchingMore={list.isFetchingNextPage}
              onLoadMore={() => void list.fetchNextPage()}
            />
          )}
        </>
      )}
    </>
  )
}
