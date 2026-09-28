import { useInfiniteQuery } from '@tanstack/react-query'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { useMemo } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin, type TeamRequestListItem, type TeamRequestsFilters } from '@/lib/api'
import { isFilterActive } from '@/lib/adminList'
import { TEAM_ORIGIN_LABELS, TEAM_REQUEST_STATES, TEAM_REQUEST_STATE_LABELS, formatDate } from '@/lib/format'
import { Empty, Header, LoadMore, StateFilter } from './lists'

function RequestRow({ item }: { item: TeamRequestListItem }) {
  return (
    <TableRow>
      <TableCell>
        <Link to="/admin/team/$id" params={{ id: item.id }} className="font-medium hover:underline">
          {item.azienda}
        </Link>
      </TableCell>
      <TableCell>{TEAM_ORIGIN_LABELS[item.origine] ?? item.origine}</TableCell>
      <TableCell className="text-muted-foreground">{formatDate(item.created_at)}</TableCell>
      <TableCell>
        <Badge variant="pill">{TEAM_REQUEST_STATE_LABELS[item.stato] ?? item.stato}</Badge>
      </TableCell>
      <TableCell className="text-right">{`${item.talenti_si} sì su ${item.talenti_totale}`}</TableCell>
    </TableRow>
  )
}

/** «Richieste team» (REB-514, spec § 3.5): every request the team builder filed, from
 *  the public page and from the cloud, newest first, with how many of the talents asked
 *  said yes. The state pills are «Talenti»'s and «Match»'s, carried in the URL
 *  (`router.tsx`); the list walks the API's cursor a page at a time, as «Talenti» does. */
export function AdminRichiesteTeam() {
  const search = useSearch({ from: '/signedIn/admin/team' })
  const navigate = useNavigate()

  function setState(stato: string | undefined) {
    void navigate({
      to: '/admin/team',
      search: (prev: TeamRequestsFilters) => ({ ...prev, stato }),
      replace: true,
    })
  }

  const filters: TeamRequestsFilters = search
  const list = useInfiniteQuery({
    queryKey: ['team-requests', filters],
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      admin.teamRequests({ ...filters, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  })
  const items = useMemo(() => list.data?.pages.flatMap((page) => page.items) ?? [], [list.data?.pages])

  return (
    <>
      <Header title="Richieste team">
        <StateFilter
          states={TEAM_REQUEST_STATES}
          value={search.stato}
          onChange={setState}
          labels={TEAM_REQUEST_STATE_LABELS}
        />
      </Header>
      {list.isError ? (
        <Empty>Non riesco a leggere la lista.</Empty>
      ) : list.isPending ? (
        <Empty>Caricamento…</Empty>
      ) : items.length === 0 ? (
        <Empty>{isFilterActive(search) ? 'Nessun risultato per questi filtri.' : 'Ancora nessuna richiesta di team.'}</Empty>
      ) : (
        <>
          <div className="px-6 pt-6 pb-6">
            <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Azienda</TableHead>
                    <TableHead>Origine</TableHead>
                    <TableHead>Arrivata</TableHead>
                    <TableHead>Stato</TableHead>
                    <TableHead className="text-right">Talenti</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((item) => (
                    <RequestRow key={item.id} item={item} />
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
          {list.hasNextPage && (
            <LoadMore
              label={`Mostrate ${items.length} richieste, ce ne sono altre.`}
              isFetchingMore={list.isFetchingNextPage}
              onLoadMore={() => void list.fetchNextPage()}
            />
          )}
        </>
      )}
    </>
  )
}
