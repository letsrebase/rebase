import { useInfiniteQuery } from '@tanstack/react-query'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin, type TeamProposalListItem, type TeamProposalsFilters } from '@/lib/api'
import { isFilterActive } from '@/lib/adminList'
import { TEAM_ORIGIN_LABELS, formatDateTime } from '@/lib/format'
import { Empty, Header, LoadMore } from './lists'

/** The two outcomes the API filters by (`LIST_OUTCOMES` in core's `team_builder.py`),
 *  and the words the pills say. */
const OUTCOMES = ['ok', 'errore'] as const
const OUTCOME_LABELS: Record<string, string> = { ok: 'Riuscite', errore: 'Fallite' }

/** `lists.tsx`'s `StateFilter`, over outcomes: «Tutte» rather than «Tutti», since the
 *  rows are proposals, its own group label, and `aria-pressed` so the chosen pill is
 *  told apart by more than its colour. */
function OutcomeFilter({ value, onChange }: { value: string | undefined; onChange: (value: string | undefined) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filtra per esito">
      {[undefined, ...OUTCOMES].map((outcome) => (
        <Button
          key={outcome ?? 'tutte'}
          type="button"
          size="sm"
          variant={value === outcome ? 'default' : 'outline'}
          aria-pressed={value === outcome}
          onClick={() => onChange(outcome)}
        >
          {outcome ? OUTCOME_LABELS[outcome] : 'Tutte'}
        </Button>
      ))}
    </div>
  )
}

/** What an attempt row says of itself (core's `TEAM_PROPOSAL_ERRORS`). */
const ERROR_LABELS: Record<string, string> = {
  llm_unavailable: 'Claude non ha risposto',
  team_builder_busy: 'Troppe richieste',
}

/** A description longer than this is shown cut, with «Mostra tutto» to read it whole. */
const PREVIEW_LENGTH = 180

function membriLabel(membri: number): string {
  return membri === 1 ? '1 persona proposta' : `${membri} persone proposte`
}

/** The description as it was typed, cut at `PREVIEW_LENGTH` until the admin asks for
 *  the whole of it: a 4,000-character project in a table cell is a column nobody can
 *  scan. */
function Descrizione({ text }: { text: string }) {
  const [whole, setWhole] = useState(false)
  const long = text.length > PREVIEW_LENGTH
  const shown = whole || !long ? text : `${text.slice(0, PREVIEW_LENGTH).trimEnd()}…`
  return (
    <div className="max-w-xl space-y-1">
      <p className="whitespace-pre-wrap">{shown}</p>
      {long && (
        <Button type="button" variant="link" size="sm" className="h-auto p-0" onClick={() => setWhole((v) => !v)}>
          {whole ? 'Mostra meno' : 'Mostra tutto'}
        </Button>
      )}
    </div>
  )
}

/** What came of the ask: the refusal on an attempt, otherwise how many people the
 *  proposal held, what it said, and the request filed on it, which is the step the
 *  list is there to count the absence of. */
function Esito({ item }: { item: TeamProposalListItem }) {
  if (item.errore) {
    return <Badge variant="pill">{`Fallita: ${ERROR_LABELS[item.errore] ?? item.errore}`}</Badge>
  }
  return (
    <div className="max-w-md space-y-1">
      <p>{item.membri === 0 ? 'Nessun profilo corrispondente' : membriLabel(item.membri)}</p>
      {item.riassunto && <p className="text-muted-foreground">{item.riassunto}</p>}
      {item.request_id ? (
        <Link to="/admin/team/$id" params={{ id: item.request_id }} className="font-medium hover:underline">
          Richiesta inviata
        </Link>
      ) : (
        <p className="text-muted-foreground">Nessuna richiesta</p>
      )}
    </div>
  )
}

function ProposalRow({ item }: { item: TeamProposalListItem }) {
  return (
    <TableRow>
      <TableCell className="whitespace-nowrap align-top text-muted-foreground">{formatDateTime(item.created_at)}</TableCell>
      <TableCell className="align-top">{TEAM_ORIGIN_LABELS[item.origine] ?? item.origine}</TableCell>
      <TableCell className="align-top">
        <Descrizione text={item.descrizione} />
        {item.nota && (
          <p className="mt-1 text-muted-foreground">
            {item.previous_id ? 'Rigenera: ' : 'Nota: '}
            {item.nota}
          </p>
        )}
        {!item.nota && item.previous_id && <p className="mt-1 text-muted-foreground">Rigenera</p>}
      </TableCell>
      <TableCell className="align-top text-right">{item.persone ?? '—'}</TableCell>
      <TableCell className="align-top">
        <Esito item={item} />
      </TableCell>
    </TableRow>
  )
}

/** «Proposte» (0028): every «Proponi il team» the public page, the cloud and an admin
 *  asked, newest first, answered or not, and whether a request followed. Ivan,
 *  2026-09-29: the asks a company never files are the measure of use. The outcome pill
 *  lives in the URL, as «Richieste team»'s state does; the list walks the API's cursor
 *  a page at a time. */
export function AdminProposteTeam() {
  const search = useSearch({ from: '/signedIn/admin/team/proposte' })
  const navigate = useNavigate()

  function setOutcome(esito: string | undefined) {
    void navigate({
      to: '/admin/team/proposte',
      search: (prev: TeamProposalsFilters) => ({ ...prev, esito }),
      replace: true,
    })
  }

  const filters: TeamProposalsFilters = search
  const list = useInfiniteQuery({
    queryKey: ['team-proposals', filters],
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      admin.teamProposals({ ...filters, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  })
  const items = useMemo(() => list.data?.pages.flatMap((page) => page.items) ?? [], [list.data?.pages])

  return (
    <>
      <Header title="Proposte">
        <OutcomeFilter value={search.esito} onChange={setOutcome} />
      </Header>
      {list.isError ? (
        <Empty>Non riesco a leggere la lista.</Empty>
      ) : list.isPending ? (
        <Empty>Caricamento…</Empty>
      ) : items.length === 0 ? (
        <Empty>{isFilterActive(search) ? 'Nessun risultato per questi filtri.' : 'Ancora nessuna proposta chiesta.'}</Empty>
      ) : (
        <>
          <div className="px-6 pt-6 pb-6">
            <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Quando</TableHead>
                    <TableHead>Origine</TableHead>
                    <TableHead>Descrizione</TableHead>
                    <TableHead className="text-right">Persone</TableHead>
                    <TableHead>Esito</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((item) => (
                    <ProposalRow key={item.id} item={item} />
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
          {list.hasNextPage && (
            <LoadMore
              label={`Mostrate ${items.length} proposte, ce ne sono altre.`}
              isFetchingMore={list.isFetchingNextPage}
              onLoadMore={() => void list.fetchNextPage()}
            />
          )}
        </>
      )}
      <p className="px-6 pb-6">
        <Link
          to="/admin/team"
          activeOptions={{ exact: true }}
          className="inline-flex items-center gap-1 text-sm underline-offset-2 hover:underline"
        >
          <ArrowLeft className="size-4" /> Tutte le richieste
        </Link>
      </p>
    </>
  )
}
