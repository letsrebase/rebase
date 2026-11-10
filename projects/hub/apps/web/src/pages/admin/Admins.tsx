import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ShieldOff, UserPlus } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { SEARCH_DEBOUNCE_MS, failureMessage, pageItems, useCursorList, useDebounce } from '@/lib/adminList'
import { ApiError, admin, type Admin } from '@/lib/api'
import { formatDate } from '@/lib/format'
import { keys } from '@/lib/query-keys'
import { Empty, Header, LoadingState, LoadMore } from './list-kit'

interface Draft {
  email: string
  first_name: string
  last_name: string
}

const EMPTY: Draft = { email: '', first_name: '', last_name: '' }

/**
 * Who reads this area, and the one form that grants or revokes the role (ORB-123,
 * REB-279): typing an email promotes whatever `users` row already answers to it, or
 * creates a bare one from `first_name`/`last_name` when none exists yet -- those two fields
 * only matter for a brand-new address, and the server ignores them harmlessly for one
 * it already knows. There is no password anywhere on this page any more: the magic
 * link is the only way in, for a member and an admin alike. Demoting is one click on
 * a row and is fully reversible, since nothing is deleted -- unlike the old
 * create/update dialog this replaces, which had no deactivation at all.
 *
 * REB-313: a debounced search box narrows the list server-side by name or email, and
 * the rest of it loads on scroll -- best-match first once searching, oldest first
 * otherwise, so the page still reads as a history with no term (ORB-123).
 */
export function AdminAdmins() {
  const client = useQueryClient()
  const [query, setQuery] = useState('')
  const debouncedQuery = useDebounce(query, SEARCH_DEBOUNCE_MS)
  const { list, items } = useCursorList({
    queryKey: keys.admins(debouncedQuery),
    fetchPage: (cursor) => admin.admins({ q: debouncedQuery || undefined, cursor }),
    itemsOf: pageItems,
  })

  const [draft, setDraft] = useState<Draft>(EMPTY)
  const [done, setDone] = useState<{ verb: 'promoted' | 'removed'; email: string } | null>(null)

  const promote = useMutation({
    mutationFn: (data: Draft) =>
      admin.promote({
        email: data.email.trim(),
        first_name: data.first_name.trim() || undefined,
        last_name: data.last_name.trim() || undefined,
      }),
    onSuccess: (saved) => {
      setDraft(EMPTY)
      setDone({ verb: 'promoted', email: saved.email })
      void client.invalidateQueries({ queryKey: keys.admins() })
    },
  })
  const demote = useMutation({
    mutationFn: (id: string) => admin.demote(id),
    onSuccess: (saved) => {
      setDone({ verb: 'removed', email: saved.email })
      void client.invalidateQueries({ queryKey: keys.admins() })
    },
  })

  const demoteFailure = failureMessage(demote.error, 'Non riesco a rimuovere questo amministratore.')
  const failure = promote.error instanceof ApiError ? promote.error : null
  const message = failure
    ? failure.message
    : promote.error
      ? 'Non riesco a promuovere questo indirizzo.'
      : null
  const wrong = (field: keyof Draft) => failure?.fields.includes(field) || undefined

  function submit(event: FormEvent) {
    event.preventDefault()
    promote.mutate(draft)
  }

  function field(name: keyof Draft) {
    return (value: string) => setDraft((current) => ({ ...current, [name]: value }))
  }

  return (
    <>
      <Header title="Amministratori" count={items.length}>
        <Input
          type="search"
          placeholder="Cerca per nome o email…"
          aria-label="Cerca amministratori"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="w-64"
        />
      </Header>

      <form
        onSubmit={submit}
        className="grid gap-4 border-b px-6 py-6 sm:grid-cols-[1fr_1fr_1fr_auto] sm:items-end"
      >
        <div className="space-y-2">
          <Label htmlFor="admin-email">Email</Label>
          <Input
            id="admin-email"
            type="email"
            required
            autoComplete="off"
            value={draft.email}
            onChange={(event) => field('email')(event.target.value)}
            aria-invalid={wrong('email')}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="admin-first-name">Nome</Label>
          <Input
            id="admin-first-name"
            maxLength={120}
            autoComplete="off"
            placeholder="Solo per un indirizzo nuovo"
            value={draft.first_name}
            onChange={(event) => field('first_name')(event.target.value)}
            aria-invalid={wrong('first_name')}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="admin-last-name">Cognome</Label>
          <Input
            id="admin-last-name"
            maxLength={120}
            autoComplete="off"
            placeholder="Solo per un indirizzo nuovo"
            value={draft.last_name}
            onChange={(event) => field('last_name')(event.target.value)}
            aria-invalid={wrong('last_name')}
          />
        </div>
        <Button type="submit" disabled={promote.isPending}>
          <UserPlus data-icon="inline-start" />
          {promote.isPending ? 'Promuovo…' : 'Promuovi'}
        </Button>
        {message && (
          <p role="alert" className="text-sm text-destructive sm:col-span-4">
            {message}
          </p>
        )}
      </form>

      {/* Always mounted: a live region that appears already filled is often not read out. */}
      <p role="status" aria-live="polite" className={done ? 'border-b px-6 py-3 text-sm' : undefined}>
        {done?.verb === 'promoted' && (
          <>
            Amministratore promosso: <span className="font-medium">{done.email}</span>.
          </>
        )}
        {done?.verb === 'removed' && (
          <>
            Non è più amministratore: <span className="font-medium">{done.email}</span>.
          </>
        )}
      </p>

      {demoteFailure && (
        <p role="alert" className="border-b px-6 py-3 text-sm text-destructive">
          {demoteFailure}
        </p>
      )}

      {list.isError ? (
        <Empty>Non riesco a leggere la lista.</Empty>
      ) : list.isPending ? (
        <LoadingState />
      ) : items.length === 0 ? (
        <Empty>{debouncedQuery ? 'Nessun risultato per questa ricerca.' : 'Nessun amministratore ancora.'}</Empty>
      ) : (
        <>
          <div className="px-6 pt-6 pb-6">
            <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
              <Table scrollLabel="Amministratori">
                <TableHeader>
                  <TableRow>
                    <TableHead>Chi</TableHead>
                    <TableHead>Stato</TableHead>
                    <TableHead className="text-right">Da quando</TableHead>
                    <TableHead>
                      <span className="sr-only">Azioni</span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((row: Admin) => (
                    <TableRow key={row.id}>
                      <TableCell>
                        <p className="font-medium">{row.first_name}</p>
                        <p className="text-xs text-muted-foreground">{row.email}</p>
                      </TableCell>
                      <TableCell>
                        <Badge variant="pill">{row.active ? 'Attivo' : 'Disattivato'}</Badge>
                      </TableCell>
                      <TableCell className="text-right text-muted-foreground">{formatDate(row.created_at)}</TableCell>
                      <TableCell className="text-right">
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          disabled={demote.isPending}
                          onClick={() => demote.mutate(row.id)}
                        >
                          <ShieldOff className="mr-2 size-4" />
                          Rimuovi
                          <span className="sr-only">
                            {' '}
                            {row.first_name} ({row.email}) da amministratore
                          </span>
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
          {list.hasNextPage && (
            <LoadMore
              label={`Mostrati ${items.length} amministratori, ce ne sono altri.`}
              isFetchingMore={list.isFetchingNextPage}
              onLoadMore={() => void list.fetchNextPage()}
            />
          )}
        </>
      )}
    </>
  )
}
