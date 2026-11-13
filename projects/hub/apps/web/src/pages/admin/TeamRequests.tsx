import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { Badge } from '@rebase/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin, type TeamRequestListItem, type TeamRequestsFilters } from '@/lib/api'
import { isFilterActive, pageItems, useCursorList } from '@/lib/adminList'
import { TEAM_ORIGIN_LABELS, TEAM_REQUEST_STATES, TEAM_REQUEST_STATE_LABELS, formatDate } from '@/lib/format'
import { keys } from '@/lib/query-keys'
import { Empty, Header, LoadingState, LoadMore, StateFilter } from './list-kit'

function RequestRow({ item }: { item: TeamRequestListItem }) {
  return (
    <TableRow>
      <TableCell>
        <Link to="/admin/team/$id" params={{ id: item.id }} className="font-medium hover:underline">
          {item.company}
        </Link>
      </TableCell>
      <TableCell>{TEAM_ORIGIN_LABELS[item.origin] ?? item.origin}</TableCell>
      <TableCell className="text-muted-foreground">{formatDate(item.created_at)}</TableCell>
      <TableCell>
        <Badge variant="pill">{TEAM_REQUEST_STATE_LABELS[item.status] ?? item.status}</Badge>
      </TableCell>
      <TableCell className="text-right">{`${item.yes_talents} sì su ${item.total_talents}`}</TableCell>
    </TableRow>
  )
}

/** «Richieste team» (REB-514, spec § 3.5): every request the team builder filed, from
 *  the public page and from the cloud, newest first, with how many of the talents asked
 *  said yes. The state pills are «Talenti»'s, carried in the URL
 *  (`router.tsx`); the list walks the API's cursor a page at a time, as «Talenti» does. */
export function AdminTeamRequests() {
  const search = useSearch({ from: '/signedIn/admin/team' })
  const navigate = useNavigate()

  function setState(status: string | undefined) {
    void navigate({
      to: '/admin/team',
      search: (prev: TeamRequestsFilters) => ({ ...prev, status }),
      replace: true,
    })
  }

  const filters: TeamRequestsFilters = search
  const { list, items } = useCursorList({
    queryKey: keys.teamRequests(filters),
    fetchPage: (cursor) => admin.teamRequests({ ...filters, cursor }),
    itemsOf: pageItems,
  })

  return (
    <>
      <Header title="Richieste team">
        <div className="flex flex-wrap items-center gap-4">
          <StateFilter
            states={TEAM_REQUEST_STATES}
            value={search.status}
            onChange={setState}
            labels={TEAM_REQUEST_STATE_LABELS}
          />
          {/* 0028: every «Proponi il team» asked, the ones never filed included. */}
          <Link to="/admin/team/proposals" className="text-sm underline-offset-2 hover:underline">
            Tutte le proposte
          </Link>
        </div>
      </Header>
      {list.isError ? (
        <Empty>Non riesco a leggere la lista.</Empty>
      ) : list.isPending ? (
        <LoadingState />
      ) : items.length === 0 ? (
        <Empty>{isFilterActive(search) ? 'Nessun risultato per questi filtri.' : 'Ancora nessuna richiesta di team.'}</Empty>
      ) : (
        <>
          <div className="px-6 pt-6 pb-6">
            <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
              <Table scrollLabel="Richieste team">
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
