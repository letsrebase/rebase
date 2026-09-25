import { useInfiniteQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { ExternalLink } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { ApiError, admin } from '@/lib/api'
import { formatDate } from '@/lib/format'
import { Empty, Header } from './lists'

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
 * Which spaces of PigroCRM exist and whose they are (ORB-142). The list is the CRM's
 * registry as the hub's API relays it; the one thing the hub adds is the member behind an
 * address, whose name links to their card. An address nobody applied with stays an
 * address, and nothing here creates, renames or deletes a space.
 *
 * REB-313: the registry's `GET /api/tenants/` takes no query parameters of its own, but
 * the hub's own route already holds the full list in memory for the request, so it
 * searches and pages it there -- a debounced search box narrows the list server-side by
 * slug or owner email, the same shape the other three admin lists this card touches use,
 * and the rest loads on scroll.
 */
export function AdminPigro() {
  const [query, setQuery] = useState('')
  const debouncedQuery = useDebounce(query, SEARCH_DEBOUNCE_MS)
  const list = useInfiniteQuery({
    queryKey: ['pigro-spaces', debouncedQuery] as const,
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      admin.pigroSpaces({ q: debouncedQuery || undefined, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })
  const items = useMemo(() => list.data?.pages.flatMap((page) => page.items) ?? [], [list.data])
  const totale = list.data?.pages[0]?.totale ?? 0
  const { hasNextPage, fetchNextPage } = list
  const failure = list.error instanceof ApiError ? list.error : null

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
      <Header title="Istanze Pigro" count={totale}>
        <Input
          type="search"
          placeholder="Cerca per spazio o email…"
          aria-label="Cerca istanze"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="w-64"
        />
      </Header>
      {list.isError ? (
        // A 503 or a 502 arrives with its own sentence; anything else gets the generic one.
        <Empty>{failure && failure.status >= 500 ? failure.message : 'Non riesco a leggere il registro.'}</Empty>
      ) : list.isPending ? (
        <Empty>Caricamento…</Empty>
      ) : items.length === 0 ? (
        <Empty>{debouncedQuery ? 'Nessun risultato per questa ricerca.' : 'Nessuna istanza ancora.'}</Empty>
      ) : (
        <>
          <div className="px-6 pt-6 pb-6">
            <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Spazio</TableHead>
                    <TableHead>Di chi è</TableHead>
                    <TableHead className="text-right">Creato</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((space) => (
                    <TableRow key={space.slug}>
                      <TableCell>
                        <a
                          // Snyk Code javascript/DOMXSS here is a false positive: the hub's
                          // API builds every url as pigro_api_url + /<slug>/app/, so the
                          // scheme is always the configured one.
                          href={space.url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1.5 font-medium hover:underline"
                        >
                          {space.slug}
                          <ExternalLink className="size-3.5 text-muted-foreground" aria-hidden="true" />
                        </a>
                      </TableCell>
                      <TableCell>
                        {space.membro ? (
                          <>
                            <Link
                              to="/admin/freelance/$id"
                              params={{ id: space.membro.id }}
                              className="font-medium hover:underline"
                            >
                              {space.membro.nome} {space.membro.cognome}
                            </Link>
                            <p className="text-xs text-muted-foreground">{space.owner_email}</p>
                          </>
                        ) : (
                          <p>{space.owner_email}</p>
                        )}
                      </TableCell>
                      <TableCell className="text-right text-muted-foreground">{formatDate(space.created_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
          <div ref={sentinelRef} />
          {hasNextPage && (
            <div className="flex flex-col items-center gap-2 px-6 py-5">
              <p className="text-sm text-muted-foreground">Mostrate {items.length} istanze, ce ne sono altre.</p>
              <Button type="button" variant="outline" size="sm" onClick={() => void fetchNextPage()}>
                Mostra altre
              </Button>
            </div>
          )}
        </>
      )}
    </>
  )
}
