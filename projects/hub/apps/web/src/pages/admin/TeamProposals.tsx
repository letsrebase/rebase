import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'
import { useState } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin, type TeamProposalListItem, type TeamProposalListMember, type TeamProposalsFilters } from '@/lib/api'
import { isFilterActive, pageItems, useCursorList } from '@/lib/adminList'
import { TEAM_ORIGIN_LABELS, formatDateTime } from '@/lib/format'
import { keys } from '@/lib/query-keys'
import { Empty, Header, LoadMore } from './list-kit'

/** The two outcomes the API filters by (`LIST_OUTCOMES` in core's `team_builder.py`),
 *  and the words the pills say. */
const OUTCOMES = ['ok', 'error'] as const
const OUTCOME_LABELS: Record<string, string> = { ok: 'Riuscite', error: 'Fallite' }

/** `list-kit.tsx`'s `StateFilter`, over outcomes: «Tutte» rather than «Tutti», since the
 *  rows are proposals, its own group label, and `aria-pressed` so the chosen pill is
 *  told apart by more than its colour. */
function OutcomeFilter({ value, onChange }: { value: string | undefined; onChange: (value: string | undefined) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filtra per esito">
      {[undefined, ...OUTCOMES].map((outcome) => (
        <Button
          key={outcome ?? 'all'}
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

function membersLabel(members: number): string {
  return members === 1 ? '1 persona proposta' : `${members} persone proposte`
}

/** A text as it was written, cut at `PREVIEW_LENGTH` until the admin asks for the
 *  whole of it: a 4,000-character description, or a 1,500-character summary, in a
 *  table cell is a column nobody can scan. */
function Clamped({ text, className }: { text: string; className?: string }) {
  const [whole, setWhole] = useState(false)
  const long = text.length > PREVIEW_LENGTH
  const shown = whole || !long ? text : `${text.slice(0, PREVIEW_LENGTH).trimEnd()}…`
  return (
    <div className="space-y-1">
      <p className={`whitespace-pre-wrap ${className ?? ''}`}>{shown}</p>
      {long && (
        <Button type="button" variant="link" size="sm" className="h-auto p-0" onClick={() => setWhole((v) => !v)}>
          {whole ? 'Mostra meno' : 'Mostra tutto'}
        </Button>
      )}
    </div>
  )
}

/** Who the proposal held (REB-607): each talent by name, linked to their admin page,
 *  with the role proposed; a talent removed with «Elimina» since has no name and no
 *  page to open, and the row says so in their place. */
function Members({ team }: { team: TeamProposalListMember[] }) {
  return (
    <ul className="space-y-0.5">
      {team.map((member) => (
        <li key={member.freelancer_id}>
          {member.first_name === null ? (
            <span className="text-muted-foreground">Profilo cancellato</span>
          ) : (
            <Link
              to="/admin/freelance/$id"
              params={{ id: member.freelancer_id }}
              className="font-medium hover:underline"
            >
              {`${member.first_name} ${member.last_name}`}
            </Link>
          )}
          <span className="text-muted-foreground">{` · ${member.role}`}</span>
        </li>
      ))}
    </ul>
  )
}

/** What came of the ask: the refusal on an attempt, otherwise how many people the
 *  proposal held and who, what it said, and the request filed on it, which is the
 *  step the list is there to count the absence of. */
function Outcome({ item }: { item: TeamProposalListItem }) {
  if (item.error) {
    return <Badge variant="pill">{`Fallita: ${ERROR_LABELS[item.error] ?? item.error}`}</Badge>
  }
  return (
    <div className="max-w-md space-y-1">
      <p>{item.members === 0 ? 'Nessun profilo corrispondente' : membersLabel(item.members)}</p>
      {item.team.length > 0 && <Members team={item.team} />}
      {item.summary && <Clamped text={item.summary} className="text-muted-foreground" />}
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
      <TableCell className="align-top">{TEAM_ORIGIN_LABELS[item.origin] ?? item.origin}</TableCell>
      <TableCell className="max-w-xl align-top">
        <Clamped text={item.description} />
        {item.note && (
          <p className="mt-1 text-muted-foreground">
            {item.previous_id ? 'Rigenera: ' : 'Nota: '}
            {item.note}
          </p>
        )}
        {!item.note && item.previous_id && <p className="mt-1 text-muted-foreground">Rigenera</p>}
      </TableCell>
      <TableCell className="align-top text-right">{item.people ?? '—'}</TableCell>
      <TableCell className="align-top">
        <Outcome item={item} />
      </TableCell>
    </TableRow>
  )
}

/** «Proposte» (0028): every «Proponi il team» the public page, the cloud and an admin
 *  asked, newest first, answered or not, and whether a request followed. Ivan,
 *  2026-09-29: the asks a company never files are the measure of use. The outcome pill
 *  lives in the URL, as «Richieste team»'s state does; the list walks the API's cursor
 *  a page at a time. */
export function AdminTeamProposals() {
  const search = useSearch({ from: '/signedIn/admin/team/proposals' })
  const navigate = useNavigate()

  function setOutcome(outcome: string | undefined) {
    void navigate({
      to: '/admin/team/proposals',
      search: (prev: TeamProposalsFilters) => ({ ...prev, outcome }),
      replace: true,
    })
  }

  const filters: TeamProposalsFilters = search
  const { list, items } = useCursorList({
    queryKey: keys.teamProposals(filters),
    fetchPage: (cursor) => admin.teamProposals({ ...filters, cursor }),
    itemsOf: pageItems,
  })

  return (
    <>
      <Header title="Proposte">
        <OutcomeFilter value={search.outcome} onChange={setOutcome} />
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
              <Table scrollLabel="Proposte di team">
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
