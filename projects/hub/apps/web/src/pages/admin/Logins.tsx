import { useState } from 'react'
import { Input } from '@rebase/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { SEARCH_DEBOUNCE_MS, useCursorList, useDebounce } from '@/lib/adminList'
import { admin, type LoginStats } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import { keys } from '@/lib/query-keys'
import { Empty, Figure, Header, LoadingState, LoadMore } from './list-kit'

/** A login's own rows, `recent`, are what the page pages through. */
const recentLogins = (page: LoginStats) => page.recent

/**
 * Who comes back in (ORB-158): every login the magic link wrote down, the distinct
 * members behind them against everybody on file, the last week, and the latest ones by
 * name. Read-only: the rows are written by `POST /api/hub/auth/enter` when a member
 * follows the link, and by nothing else.
 *
 * REB-313: the four counters above stay a plain read of the whole table, unaffected by
 * search; «i più recenti» below them is what a debounced search box narrows and what
 * loads on scroll, no longer a hardcoded top 20 -- best-match first once searching,
 * newest first otherwise. A login's `user_id` is not necessarily a freelancer's own
 * id (a company contact or another admin can sign in too), so a row names the person
 * without assuming their card exists to link to.
 */
export function AdminLogins() {
  const [query, setQuery] = useState('')
  const debouncedQuery = useDebounce(query, SEARCH_DEBOUNCE_MS)
  const { list: stats, items: recent } = useCursorList({
    queryKey: keys.loginStats(debouncedQuery),
    fetchPage: (cursor) => admin.loginStats({ q: debouncedQuery || undefined, cursor }),
    itemsOf: recentLogins,
  })
  const totals = stats.data?.pages[0]

  return (
    <>
      <Header title="Accessi" count={totals?.total}>
        <Input
          type="search"
          placeholder="Cerca per nome o email…"
          aria-label="Cerca accessi"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="w-64"
        />
      </Header>
      {stats.isError ? (
        <Empty>Non riesco a leggere gli accessi.</Empty>
      ) : stats.isPending || !totals ? (
        <LoadingState />
      ) : (
        <>
          <dl className="grid gap-4 border-b px-6 py-5 sm:grid-cols-3">
            <Figure label="Accessi" value={totals.total} />
            <Figure label="Membri entrati" value={totals.members} note={`su ${totals.total_members}`} />
            <Figure label="Ultimi 7 giorni" value={totals.last_7_days} />
          </dl>
          {recent.length === 0 ? (
            <Empty>{debouncedQuery ? 'Nessun risultato per questa ricerca.' : 'Nessun accesso ancora.'}</Empty>
          ) : (
            <>
              <div className="px-6 pt-6 pb-6">
                <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
                  <Table scrollLabel="Ultimi accessi">
                    <TableHeader>
                      <TableRow>
                        <TableHead>Chi</TableHead>
                        <TableHead className="text-right">Quando</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {recent.map((login) => (
                        <TableRow key={login.id}>
                          <TableCell>
                            <p className="font-medium">
                              {login.first_name} {login.last_name}
                            </p>
                            <p className="text-xs text-muted-foreground">{login.email}</p>
                          </TableCell>
                          <TableCell className="text-right text-muted-foreground">
                            {formatDateTime(login.logged_at)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </div>
              {stats.hasNextPage && (
                <LoadMore
                  label={`Mostrati ${recent.length} accessi, ce ne sono altri.`}
                  isFetchingMore={stats.isFetchingNextPage}
                  onLoadMore={() => void stats.fetchNextPage()}
                />
              )}
            </>
          )}
        </>
      )}
    </>
  )
}
