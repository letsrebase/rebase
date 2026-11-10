import { LoadMore } from '@/components/LoadMore'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { toProblem } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@rebase/ui/sheet'
import { toast } from '@rebase/ui/sonner'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@rebase/ui/table'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import {
  LINK_LABELS,
  LINK_TONES,
  formatDateTime,
  formatDay,
  linkStateOf,
  openingsWord,
  type ShareLink,
} from './labels'
import { periodName } from './periods'
import { useAllLinks, useBuilderNames, useCompanyName, useLink, useRevokeLink } from './queries'

/** The table from `md` up, the inline panel from `lg`: below them rows are cards and the panel
 *  is a full-screen sheet (the brief: phone first). */
const TABLE = '(min-width: 768px)'
const INLINE_PANEL = '(min-width: 1024px)'

function Company({ name }: { name: string }) {
  return (
    <p className="max-w-72 truncate font-medium" title={name}>
      {name}
    </p>
  )
}

/** «Revoca»: ends the link on the click, with a toast that says what happened and no dialog. */
function RevokeButton({ link }: { link: ShareLink }) {
  const revoke = useRevokeLink()
  if (linkStateOf(link) !== 'active') return null
  return (
    <Button
      size="sm"
      variant="outline"
      disabled={revoke.isPending}
      onClick={() =>
        revoke.mutate(link.id, {
          onSuccess: () => toast.success('Link revocato: chi lo ha non scarica più'),
          onError: (error) => toast.error(toProblem(error).detail),
        })
      }
    >
      Revoca
    </Button>
  )
}

/** The open link: its export, who made it and until when, every opening (when, which file, from
 *  which address) and «Revoca». The address of the link itself is not here: it was shown once. */
function LinkPanel({
  id,
  onClose,
  onOpenExport,
}: {
  id: string
  onClose: () => void
  onOpenExport: (exportId: string) => void
}) {
  const detail = useLink(id)
  const companyName = useCompanyName()
  const builder = useBuilderNames()
  const { user } = useAuth()
  if (detail.isError) {
    return (
      <div className="space-y-3 p-4">
        <QueryErrorBanner error={detail.error} />
        <Button variant="outline" size="sm" onClick={onClose}>
          Chiudi
        </Button>
      </div>
    )
  }
  if (!detail.data) return <LoadingState label="Leggo il link…" className="p-4" />
  const link = detail.data
  const state = linkStateOf(link)
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 space-y-6 overflow-y-auto p-4">
        <header className="space-y-2">
          <h2 className="break-words text-lg font-semibold">{companyName(link)}</h2>
          <p className="text-sm">{periodName(link.period_from, link.period_to)}</p>
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill tone={LINK_TONES[state]}>{LINK_LABELS[state]}</StatusPill>
          </div>
          <p className="text-xs text-muted-foreground">
            Creato il {formatDateTime(link.created_at)} da {builder(link.created_by, user?.id ?? null).by}
          </p>
          <p className="text-xs text-muted-foreground">
            {state === 'revoked' && link.revoked_at !== null
              ? `Revocato il ${formatDateTime(link.revoked_at)}`
              : `${state === 'expired' ? 'Scaduto' : 'Scade'} il ${formatDay(link.expires_at)}`}
          </p>
        </header>

        <section aria-label="Aperture" className="space-y-2">
          <h3 className="text-sm font-semibold">
            Aperture <span className="tabular-nums text-muted-foreground">{link.download_count}</span>
          </h3>
          {link.openings.length === 0 ? (
            <p className="text-sm text-muted-foreground">{openingsWord(0)}.</p>
          ) : (
            <ul className="divide-y border">
              {link.openings.map((opening) => (
                <li
                  key={`${opening.opened_at}-${opening.part}-${opening.address}`}
                  className="flex flex-wrap justify-between gap-x-3 px-3 py-2 text-sm"
                >
                  <span className="tabular-nums">{formatDateTime(opening.opened_at)}</span>
                  <span className="break-all text-muted-foreground">
                    {opening.address} · ZIP {opening.part}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
      <footer className="flex flex-wrap gap-2 border-t bg-card p-4">
        <RevokeButton link={link} />
        <Button variant="outline" size="sm" onClick={() => onOpenExport(link.export_id)}>
          Apri l’esportazione
        </Button>
      </footer>
    </div>
  )
}

/**
 * «Link condivisi» (REB-1047, the approved brief, answer 5): every share link of the space as a
 * gridded table with the open one in a panel beside it from `lg`, cards and a full-screen sheet
 * below. Expired and revoked links stay as muted rows. Nothing here can copy a link: its address
 * was shown once, when it was made.
 */
export function LinksTab({
  link,
  onLinkChange,
  onOpenExport,
  onPrepareExports,
}: {
  link: string | null
  onLinkChange: (next: string | null) => void
  onOpenExport: (exportId: string) => void
  onPrepareExports: () => void
}) {
  const wide = useMediaQuery(TABLE)
  const inline = useMediaQuery(INLINE_PANEL)
  const list = useAllLinks()
  const companyName = useCompanyName()

  const items = list.data?.pages.flatMap((page) => page.items) ?? []
  const total = list.data?.pages.at(-1)?.total_count ?? 0

  const subtitle = (row: ShareLink) =>
    `${periodName(row.period_from, row.period_to)} · creato il ${formatDay(row.created_at)}`
  const muted = (row: ShareLink) => linkStateOf(row) !== 'active' && 'text-muted-foreground'

  const body = list.isError && !list.isFetchNextPageError ? (
    <QueryErrorBanner error={list.error} onRetry={() => void list.refetch()} />
  ) : list.isPending ? (
    <LoadingState label="Leggo i link…" className="py-8" />
  ) : items.length === 0 ? (
    <div className="border border-dashed px-6 py-12 text-center">
      <p className="text-base font-semibold">Nessun link</p>
      <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">
        Qui trovi i link creati per il commercialista, con quante volte sono stati aperti. Si creano
        dal pannello di un’esportazione.
      </p>
      <Button className="mt-4" onClick={onPrepareExports}>
        Vai alle esportazioni
      </Button>
    </div>
  ) : (
    <div className="space-y-4">
      {wide ? (
        <div className="overflow-x-auto overflow-y-hidden border bg-card">
          <Table scrollLabel="Link condivisi">
            <TableHeader>
              <TableRow>
                <TableHead>Esportazione</TableHead>
                <TableHead>Scade</TableHead>
                <TableHead className="text-right">Aperture</TableHead>
                <TableHead>Stato</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((row) => {
                const state = linkStateOf(row)
                return (
                  <TableRow
                    key={row.id}
                    data-state={row.id === link ? 'selected' : undefined}
                    aria-selected={row.id === link}
                    tabIndex={0}
                    className={cn(
                      'cursor-pointer align-top focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
                      muted(row),
                    )}
                    onClick={() => onLinkChange(row.id)}
                    onKeyDown={(event) => {
                      if (event.target !== event.currentTarget) return
                      if (event.key !== 'Enter' && event.key !== ' ') return
                      event.preventDefault()
                      onLinkChange(row.id)
                    }}
                  >
                    <TableCell>
                      <Company name={companyName(row)} />
                      <p className="text-xs text-muted-foreground">{subtitle(row)}</p>
                    </TableCell>
                    <TableCell className="whitespace-nowrap tabular-nums">
                      {formatDay(row.expires_at)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{row.download_count}</TableCell>
                    <TableCell>
                      <StatusPill tone={LINK_TONES[state]}>{LINK_LABELS[state]}</StatusPill>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      ) : (
        <ul className="space-y-3" aria-label="Link condivisi">
          {items.map((row) => {
            const state = linkStateOf(row)
            return (
              <li key={row.id}>
                <button
                  type="button"
                  onClick={() => onLinkChange(row.id)}
                  className={cn(
                    'block min-h-24 w-full space-y-2 border bg-card p-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                    muted(row),
                  )}
                >
                  <Company name={companyName(row)} />
                  <p className="text-xs text-muted-foreground">{subtitle(row)}</p>
                  <p className="text-xs tabular-nums text-muted-foreground">
                    Scade il {formatDay(row.expires_at)} · {openingsWord(row.download_count)}
                  </p>
                  <StatusPill tone={LINK_TONES[state]}>{LINK_LABELS[state]}</StatusPill>
                </button>
              </li>
            )
          })}
        </ul>
      )}
      {list.hasNextPage ? (
        <LoadMore
          label="Mostra altri"
          message={`Mostrati ${items.length} di ${total} link.`}
          isFetchingMore={list.isFetchingNextPage}
          hasError={list.isFetchNextPageError}
          error={list.error}
          onLoadMore={() => void list.fetchNextPage()}
        />
      ) : null}
    </div>
  )

  return (
    <>
      <div className={cn('flex gap-6 px-8 pb-6 pt-4', inline ? 'min-h-0 flex-1' : 'items-start')}>
        <div className={cn('min-w-0 flex-1', inline && 'h-full overflow-y-auto')}>{body}</div>
        {link !== null && inline ? (
          <aside
            aria-label="Link aperto"
            className="flex h-full w-82.5 shrink-0 flex-col overflow-hidden border bg-card"
          >
            <div className="flex justify-end border-b px-2 py-1">
              <Button variant="ghost" size="sm" onClick={() => onLinkChange(null)}>
                Chiudi
              </Button>
            </div>
            <LinkPanel id={link} onClose={() => onLinkChange(null)} onOpenExport={onOpenExport} />
          </aside>
        ) : null}
      </div>

      {link !== null && !inline ? (
        <Sheet open onOpenChange={(open) => !open && onLinkChange(null)}>
          <SheetContent
            side="right"
            closeLabel="Chiudi"
            className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-none"
          >
            <SheetTitle className="sr-only">Link aperto</SheetTitle>
            <SheetDescription className="sr-only">
              Quando scade, quante volte è stato aperto e come revocarlo.
            </SheetDescription>
            <div className="flex min-h-0 flex-1 flex-col pt-12">
              <LinkPanel id={link} onClose={() => onLinkChange(null)} onOpenExport={onOpenExport} />
            </div>
          </SheetContent>
        </Sheet>
      ) : null}
    </>
  )
}
