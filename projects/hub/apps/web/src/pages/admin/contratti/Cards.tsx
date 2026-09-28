import { Link } from '@tanstack/react-router'
import { ChevronDown, Download } from 'lucide-react'
import { useRef, type ReactNode } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@rebase/ui/card'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@rebase/ui/dropdown-menu'
import { admin, type Action, type ContractDocument, type Match } from '@/lib/api'
import { MATCHES_HEADING_ID, QUADRO_HEADING_ID, matchHeadingId, whatOf } from '@/lib/contracts'
import {
  ACTION_LABELS,
  ACTION_PENDING_LABELS,
  DOCUMENT_STATE_LABELS,
  MATCH_STATE_LABELS,
  formatDate,
} from '@/lib/format'
import { Row } from '../lists'

/** What the page does when the admin picks one of the core's actions on a card. */
export interface ActionHandle {
  /** What a screen reader hears: the button's words plus the object, since every card
   *  carries the same words (REB-407). */
  label: string
  /** The same while the request runs, so the name carries the progress the words show. */
  pendingLabel: string
  /** `from` is the control the admin used, where a question asked first gives the focus
   *  back when dismissed: the button, or «Altre azioni» for an item of its menu. */
  run: (from: HTMLElement | null) => void
  /** This card's own request for this action is in flight. */
  pending: boolean
}
export type ActionHandles = Partial<Record<Action, ActionHandle>>

// The ones that ask before acting, and cannot be taken back from the page.
const DESTRUCTIVE: ReadonlySet<Action> = new Set<Action>(['annulla', 'chiudi', 'registra_disdetta'])

function DocumentLinks({ document }: { document: ContractDocument }) {
  const what = whatOf(document)
  return (
    <>
      <Button asChild variant="outline" size="sm">
        <a href={admin.contractPdfUrl(document.id)} aria-label={`PDF ${what}`}>
          <Download aria-hidden="true" />
          PDF
        </a>
      </Button>
      {document.ha_pdf_firmato && (
        <Button asChild variant="outline" size="sm">
          <a href={admin.contractPdfUrl(document.id, true)} aria-label={`PDF firmato ${what}`}>
            <Download aria-hidden="true" />
            PDF firmato
          </a>
        </Button>
      )}
    </>
  )
}

/** «Consuntivo» (REB-498): the hours of a match linked to its deal on Pigro, on the
 *  admin's own page. */
function ReportLink({ match }: { match: Match }) {
  return (
    <Button asChild variant="outline" size="sm">
      <Link
        to="/admin/matches/$id/report"
        params={{ id: match.id }}
        aria-label={`Consuntivo del match con ${match.nome_azienda} come ${match.figura_richiesta}`}
      >
        Consuntivo
      </Link>
    </Button>
  )
}

/** The one step the core says comes next, as the card's button, the PDFs, and every
 *  other action the item has in its state behind «Altre azioni». The page decides none
 *  of them; an action with no call on this kind of card, which the core never names,
 *  is left out. */
function NextSteps({
  next,
  others,
  handles,
  busy,
  moreLabel,
  children,
}: {
  next: Action | null
  others: Action[]
  handles: ActionHandles
  busy: boolean
  moreLabel: string
  children: ReactNode
}) {
  const primary = next ? handles[next] : undefined
  const more = others.flatMap((action) => {
    const handle = handles[action]
    return handle ? [{ action, handle }] : []
  })
  // The menu closes on a pick, so its trigger is what says the request is running.
  const running = more.find(({ handle }) => handle.pending)
  const trigger = useRef<HTMLButtonElement>(null)
  return (
    <div className="flex flex-wrap items-center gap-2">
      {next && primary && (
        <Button
          type="button"
          size="sm"
          disabled={busy}
          aria-label={primary.pending ? primary.pendingLabel : primary.label}
          onClick={(event) => primary.run(event.currentTarget)}
        >
          {primary.pending ? ACTION_PENDING_LABELS[next] : ACTION_LABELS[next]}
        </Button>
      )}
      {children}
      {more.length > 0 && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              ref={trigger}
              type="button"
              variant="ghost"
              size="sm"
              disabled={busy}
              aria-label={running ? running.handle.pendingLabel : moreLabel}
            >
              {running ? ACTION_PENDING_LABELS[running.action] : 'Altre azioni'}
              <ChevronDown aria-hidden="true" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-auto">
            {more.map(({ action, handle }) => (
              <DropdownMenuItem
                key={action}
                variant={DESTRUCTIVE.has(action) ? 'destructive' : 'default'}
                aria-label={handle.label}
                onSelect={() => handle.run(trigger.current)}
              >
                {ACTION_LABELS[action]}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  )
}

function Failure({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <p role="alert" className="text-sm text-destructive">
      {message}
    </p>
  )
}

/** «Contratto quadro»: where it stands in one sentence and what to do next; the text's
 *  version, read rarely, in a closed «Dettagli». Its own errors show under it. */
export function FrameworkCard({
  quadro,
  handles,
  busy,
  error,
}: {
  quadro: ContractDocument | null
  handles: ActionHandles
  busy: boolean
  error: string | null
}) {
  return (
    <section aria-labelledby={QUADRO_HEADING_ID} className="space-y-2">
      <Card>
        <CardHeader>
          <CardTitle>
            {/* Focusable by the page alone, to land on when an action took its control away. */}
            <h2 id={QUADRO_HEADING_ID} tabIndex={-1}>
              Contratto quadro
            </h2>
          </CardTitle>
          {quadro && (
            <CardAction>
              <Badge variant="pill">{DOCUMENT_STATE_LABELS[quadro.stato] ?? quadro.stato}</Badge>
            </CardAction>
          )}
        </CardHeader>
        <CardContent className="space-y-3">
          {quadro === null ? (
            <p className="text-muted-foreground">Nessun contratto quadro: parte con il primo match inviato.</p>
          ) : (
            <>
              <p>{quadro.situazione}</p>
              <NextSteps
                next={quadro.prossima_azione}
                others={quadro.altre_azioni}
                handles={handles}
                busy={busy}
                moreLabel="Altre azioni del contratto quadro"
              >
                <DocumentLinks document={quadro} />
              </NextSteps>
              <details className="text-muted-foreground">
                <summary className="cursor-pointer text-xs">Dettagli</summary>
                <dl className="mt-3 text-sm">
                  <Row label="Versione del testo">
                    <span className="flex flex-wrap items-center gap-2">
                      <span>{quadro.text_version}</span>
                      {quadro.nuova_versione && <Badge variant="pill">Nuova versione disponibile</Badge>}
                    </span>
                  </Row>
                </dl>
              </details>
            </>
          )}
        </CardContent>
      </Card>
      <Failure message={error} />
    </section>
  )
}

/** One match: the company and the role, where it stands, and its next step. */
function MatchCard({ match, handles, busy }: { match: Match; handles: ActionHandles; busy: boolean }) {
  const titleId = matchHeadingId(match.id)
  return (
    <article aria-labelledby={titleId}>
      <Card>
        <CardHeader>
          <CardTitle>
            <h3 id={titleId} tabIndex={-1}>{`${match.nome_azienda} · ${match.figura_richiesta}`}</h3>
          </CardTitle>
          <CardAction>
            <Badge variant="pill">{MATCH_STATE_LABELS[match.stato] ?? match.stato}</Badge>
          </CardAction>
        </CardHeader>
        <CardContent className="space-y-3">
          <p>{match.situazione}</p>
          <NextSteps
            next={match.prossima_azione}
            others={match.altre_azioni}
            handles={handles}
            busy={busy}
            moreLabel={`Altre azioni del match con ${match.nome_azienda} come ${match.figura_richiesta}`}
          >
            <DocumentLinks document={match.lettera} />
            {match.pigro_stato === 'collegato' && <ReportLink match={match} />}
          </NextSteps>
          <p className="text-xs text-muted-foreground">{`Creato il ${formatDate(match.created_at)}`}</p>
        </CardContent>
      </Card>
    </article>
  )
}

/** «Match»: a card per match, newest first as the API answers them. A match's errors
 *  show under the cards. */
export function MatchCards({
  matches,
  handlesFor,
  busy,
  error,
}: {
  matches: Match[]
  handlesFor: (match: Match) => ActionHandles
  busy: boolean
  error: string | null
}) {
  return (
    <section aria-labelledby={MATCHES_HEADING_ID} className="space-y-3">
      <h2 id={MATCHES_HEADING_ID} tabIndex={-1} className="text-sm font-medium">
        Match
      </h2>
      {matches.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nessun match per questa persona.</p>
      ) : (
        matches.map((match) => (
          <MatchCard key={match.id} match={match} handles={handlesFor(match)} busy={busy} />
        ))
      )}
      <Failure message={error} />
    </section>
  )
}
