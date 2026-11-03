import { Link } from '@tanstack/react-router'
import { ExternalLink } from 'lucide-react'
import { useState } from 'react'
import { Input } from '@rebase/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { WebLink } from '@/components/WebLink'
import { SEARCH_DEBOUNCE_MS, pageItems, useCursorList, useDebounce } from '@/lib/adminList'
import { ApiError, admin } from '@/lib/api'
import { formatDate } from '@/lib/format'
import { keys } from '@/lib/query-keys'
import { isHttpUrl } from '@/lib/url'
import { Empty, Header, LoadMore } from './list-kit'

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
  const { list, items } = useCursorList({
    queryKey: keys.pigroSpaces(debouncedQuery),
    fetchPage: (cursor) => admin.pigroSpaces({ q: debouncedQuery || undefined, cursor }),
    itemsOf: pageItems,
  })
  const total = list.data?.pages[0]?.total ?? 0
  const failure = list.error instanceof ApiError ? list.error : null

  return (
    <>
      <Header title="Istanze Pigro" count={total}>
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
              <Table scrollLabel="Istanze Pigro">
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
                        {isHttpUrl(space.url) ? (
                          <WebLink
                            href={space.url}
                            className="inline-flex items-center gap-1.5 font-medium no-underline hover:underline"
                          >
                            {space.slug}
                            <ExternalLink className="size-3.5 text-muted-foreground" aria-hidden="true" />
                          </WebLink>
                        ) : (
                          <span className="font-medium">{space.slug}</span>
                        )}
                      </TableCell>
                      <TableCell>
                        {space.member ? (
                          <>
                            <Link
                              to="/admin/freelance/$id"
                              params={{ id: space.member.id }}
                              className="font-medium hover:underline"
                            >
                              {space.member.nome} {space.member.last_name}
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
          {list.hasNextPage && (
            <LoadMore
              label={`Mostrate ${items.length} istanze, ce ne sono altre.`}
              isFetchingMore={list.isFetchingNextPage}
              onLoadMore={() => void list.fetchNextPage()}
            />
          )}
        </>
      )}
    </>
  )
}
