import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ShieldOff, UserPlus } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { ApiError, admin, type Admin } from '@/lib/api'
import { formatDate } from '@/lib/format'
import { Empty, Header } from './lists'

const ADMINS_KEY = ['admins'] as const
const SEARCH_DEBOUNCE_MS = 300

interface Draft {
  email: string
  nome: string
  cognome: string
}

const EMPTY: Draft = { email: '', nome: '', cognome: '' }

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
 * Who reads this area, and the one form that grants or revokes the role (ORB-123,
 * REB-279): typing an email promotes whatever `users` row already answers to it, or
 * creates a bare one from `nome`/`cognome` when none exists yet -- those two fields
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
  const list = useInfiniteQuery({
    queryKey: [...ADMINS_KEY, debouncedQuery] as const,
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      admin.admins({ q: debouncedQuery || undefined, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })
  const items = useMemo(() => list.data?.pages.flatMap((page) => page.items) ?? [], [list.data])
  const { hasNextPage, fetchNextPage } = list

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

  const [draft, setDraft] = useState<Draft>(EMPTY)
  const [done, setDone] = useState<{ verb: 'promosso' | 'rimosso'; email: string } | null>(null)

  const promote = useMutation({
    mutationFn: (data: Draft) =>
      admin.promote({
        email: data.email.trim(),
        nome: data.nome.trim() || undefined,
        cognome: data.cognome.trim() || undefined,
      }),
    onSuccess: (saved) => {
      setDraft(EMPTY)
      setDone({ verb: 'promosso', email: saved.email })
      void client.invalidateQueries({ queryKey: ADMINS_KEY })
    },
  })
  const demote = useMutation({
    mutationFn: (id: string) => admin.demote(id),
    onSuccess: (saved) => {
      setDone({ verb: 'rimosso', email: saved.email })
      void client.invalidateQueries({ queryKey: ADMINS_KEY })
    },
  })

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
          <Label htmlFor="admin-nome">Nome</Label>
          <Input
            id="admin-nome"
            maxLength={120}
            autoComplete="off"
            placeholder="Solo per un indirizzo nuovo"
            value={draft.nome}
            onChange={(event) => field('nome')(event.target.value)}
            aria-invalid={wrong('nome')}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="admin-cognome">Cognome</Label>
          <Input
            id="admin-cognome"
            maxLength={120}
            autoComplete="off"
            placeholder="Solo per un indirizzo nuovo"
            value={draft.cognome}
            onChange={(event) => field('cognome')(event.target.value)}
            aria-invalid={wrong('cognome')}
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
        {done?.verb === 'promosso' && (
          <>
            Amministratore promosso: <span className="font-medium">{done.email}</span>.
          </>
        )}
        {done?.verb === 'rimosso' && (
          <>
            Non è più amministratore: <span className="font-medium">{done.email}</span>.
          </>
        )}
      </p>

      {list.isError ? (
        <Empty>Non riesco a leggere la lista.</Empty>
      ) : list.isPending ? (
        <Empty>Caricamento…</Empty>
      ) : items.length === 0 ? (
        <Empty>{debouncedQuery ? 'Nessun risultato per questa ricerca.' : 'Nessun amministratore ancora.'}</Empty>
      ) : (
        <>
          <div className="px-6 pb-6">
            <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
              <Table>
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
                        <p className="font-medium">{row.nome}</p>
                        <p className="text-xs text-muted-foreground">{row.email}</p>
                      </TableCell>
                      <TableCell>
                        <Badge variant="pill">{row.attivo ? 'Attivo' : 'Disattivato'}</Badge>
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
                            {row.nome} ({row.email}) da amministratore
                          </span>
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
          <div ref={sentinelRef} />
          {list.hasNextPage && (
            <div className="flex flex-col items-center gap-2 px-6 py-5">
              <p className="text-sm text-muted-foreground">
                Mostrati {items.length} amministratori, ce ne sono altri.
              </p>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={list.isFetchingNextPage}
                onClick={() => void list.fetchNextPage()}
              >
                {list.isFetchingNextPage ? 'Carico…' : 'Mostra altri'}
              </Button>
            </div>
          )}
        </>
      )}
    </>
  )
}
