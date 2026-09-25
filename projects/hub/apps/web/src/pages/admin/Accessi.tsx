import { useInfiniteQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Loader } from '@rebase/ui/loader'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import { Empty, Figure, Header } from './lists'

const LOGINS_KEY = ['login-stats'] as const
const SEARCH_DEBOUNCE_MS = 300

/** The value it settles to `delayMs` after the caller stops changing it -- the search
 *  box's own text, so a query is not sent on every keystroke (REB-313). */
function useDebounce<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(timer)
  }, [value, delayMs])
  return debounced
}

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
export function AdminAccessi() {
  const [query, setQuery] = useState('')
  const debouncedQuery = useDebounce(query, SEARCH_DEBOUNCE_MS)
  const stats = useInfiniteQuery({
    queryKey: [...LOGINS_KEY, debouncedQuery] as const,
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      admin.loginStats({ q: debouncedQuery || undefined, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })
  const totals = stats.data?.pages[0]
  const recenti = useMemo(() => stats.data?.pages.flatMap((page) => page.recenti) ?? [], [stats.data])
  const { hasNextPage, fetchNextPage } = stats

  const sentinelRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (!hasNextPage || typeof IntersectionObserver === 'undefined') return
    const node = sentinelRef.current
    if (!node) return
    const observer = new IntersectionObserver((entries) => {
      if (entries[0]?.isIntersecting) void fetchNextPage()
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [hasNextPage, fetchNextPage])

  return (
    <>
      <Header title="Accessi" count={totals?.totale}>
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
        <Empty>
          <span className="inline-flex items-center gap-2">
            <Loader className="size-4" />
            Caricamento…
          </span>
        </Empty>
      ) : (
        <>
          <dl className="grid gap-4 border-b px-6 py-5 sm:grid-cols-3">
            <Figure label="Accessi" value={totals.totale} />
            <Figure label="Membri entrati" value={totals.membri} note={`su ${totals.membri_totali}`} />
            <Figure label="Ultimi 7 giorni" value={totals.ultimi_7_giorni} />
          </dl>
          {recenti.length === 0 ? (
            <Empty>{debouncedQuery ? 'Nessun risultato per questa ricerca.' : 'Nessun accesso ancora.'}</Empty>
          ) : (
            <>
              <div className="px-6 pt-6 pb-6">
                <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Chi</TableHead>
                        <TableHead className="text-right">Quando</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {recenti.map((login) => (
                        <TableRow key={login.id}>
                          <TableCell>
                            <p className="font-medium">
                              {login.nome} {login.cognome}
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
              <div ref={sentinelRef} />
              {stats.hasNextPage && (
                <div className="flex flex-col items-center gap-2 px-6 py-5">
                  <p className="text-sm text-muted-foreground">Mostrati {recenti.length} accessi, ce ne sono altri.</p>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={stats.isFetchingNextPage}
                    onClick={() => void stats.fetchNextPage()}
                  >
                    {stats.isFetchingNextPage ? 'Carico…' : 'Mostra altri'}
                  </Button>
                </div>
              )}
            </>
          )}
        </>
      )}
    </>
  )
}
