import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from '@tanstack/react-router'
import { useState } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin, ApiError, type CampaignRecipient } from '@/lib/api'
import {
  ACTION_DONE_LABELS,
  RECIPIENT_STATE_LABELS,
  campaignMoment,
  campaignStateLabel,
  isStalled,
  refetchEvery,
  share,
  stallLine,
} from '@/lib/campaigns'
import { formatDateTime } from '@/lib/format'
import { keys } from '@/lib/query-keys'
import { Empty, Figure, Header } from './list-kit'

/** An action behind an inline second click, in place of a browser `confirm()`: the
 *  first click swaps the button for the question, the second one runs it. «Annulla»,
 *  «Non scrivere mai» and «Elimina» (here and in «Campagne») all use it -- none can be
 *  undone. */
export function ConfirmAction({
  label,
  question,
  pendingLabel,
  pending,
  onConfirm,
  variant,
}: {
  label: string
  question: string
  pendingLabel: string
  pending: boolean
  onConfirm: () => void
  variant: 'destructive' | 'outline'
}) {
  const [asking, setAsking] = useState(false)
  if (!asking) {
    return (
      <Button type="button" variant={variant} size="sm" onClick={() => setAsking(true)}>
        {label}
      </Button>
    )
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-sm">{question}</span>
      <Button type="button" variant="destructive" size="sm" disabled={pending} onClick={() => onConfirm()}>
        {pending ? pendingLabel : 'Conferma'}
      </Button>
      <Button type="button" variant="outline" size="sm" onClick={() => setAsking(false)} disabled={pending}>
        Indietro
      </Button>
    </div>
  )
}

/** A row's own «Non scrivere mai» (REB-473), behind the same second click as
 *  «Annulla»: the opt-out is undone only by the person writing back in, so a stray
 *  click on the wrong row must not settle it. The row keeps `email` after success,
 *  so the page has something to render the fixed sentence beside. */
function NeverWriteCell({ email }: { email: string }) {
  const [done, setDone] = useState(false)
  const neverWrite = useMutation({
    mutationFn: () => admin.neverWrite(email),
    onSuccess: () => setDone(true),
  })
  if (done) return <span className="text-sm text-muted-foreground">Non riceverà più campagne</span>
  const failure =
    neverWrite.error instanceof ApiError
      ? neverWrite.error.message
      : neverWrite.error
        ? 'Non riesco a registrare la scelta.'
        : null
  return (
    <div className="space-y-1">
      <ConfirmAction
        label="Non scrivere mai"
        question="Non scrivere più a questa persona?"
        pendingLabel="Registro…"
        pending={neverWrite.isPending}
        onConfirm={() => neverWrite.mutate()}
        variant="outline"
      />
      {failure && (
        <p role="alert" className="text-xs text-destructive">
          {failure}
        </p>
      )}
    </div>
  )
}

type Filter = 'all' | 'action' | 'nothing'

const FILTERS: [Filter, string][] = [
  ['all', 'Tutti'],
  ['action', 'Ha fatto l’azione'],
  ['nothing', 'Non ha fatto niente'],
]

/** Whom the mail reached with no action after it: the follow-up list for those who did
 *  nothing starts from (spec § 4.3). A bounced mail reached nobody and a complaint is a
 *  never-again, so neither counts, as the server's `waiting_rows` (REB-524). */
function didNothing(recipient: CampaignRecipient): boolean {
  return (
    recipient.status === 'sent' &&
    recipient.action_at === null &&
    recipient.bounced_at === null &&
    recipient.complaint_at === null
  )
}

function matches(recipient: CampaignRecipient, filter: Filter): boolean {
  if (filter === 'action') return recipient.action_at !== null
  if (filter === 'nothing') return didNothing(recipient)
  return true
}

function Moment({ at, fromMail = false }: { at: string | null; fromMail?: boolean }) {
  if (!at) return null
  return (
    <>
      <p>{formatDateTime(at)}</p>
      {fromMail && <p className="text-xs">dalla mail</p>}
    </>
  )
}

function RecipientRow({ recipient }: { recipient: CampaignRecipient }) {
  return (
    <TableRow>
      <TableCell>
        <p className="font-medium">{recipient.name ?? '—'}</p>
        <p className="text-xs text-muted-foreground">{recipient.email}</p>
      </TableCell>
      <TableCell>
        <p>{RECIPIENT_STATE_LABELS[recipient.status]}</p>
        {recipient.sent_at && <p className="text-xs text-muted-foreground">{formatDateTime(recipient.sent_at)}</p>}
        {recipient.reason && <p className="text-xs text-muted-foreground">{recipient.reason}</p>}
      </TableCell>
      <TableCell className="text-muted-foreground">
        {recipient.bounced_at ? (
          <>
            <p className="text-destructive">Rimbalzata</p>
            <p className="text-xs">{formatDateTime(recipient.bounced_at)}</p>
          </>
        ) : (
          <Moment at={recipient.delivered_at} />
        )}
      </TableCell>
      <TableCell className="text-muted-foreground">
        <Moment at={recipient.first_click_at} />
      </TableCell>
      <TableCell className="text-muted-foreground">
        <Moment at={recipient.signed_in_at} fromMail={recipient.signed_in_from_mail} />
      </TableCell>
      <TableCell className="text-muted-foreground">
        <Moment at={recipient.action_at} fromMail={recipient.action_from_mail} />
      </TableCell>
      <TableCell>
        <NeverWriteCell email={recipient.email} />
      </TableCell>
    </TableRow>
  )
}

/** «Campagna» (P-REB-41): when it leaves or left, the numbers, the recipients and
 *  what an admin can still undo from here -- «Modifica» and «Elimina» on a draft,
 *  «Riporta in bozza»/«Annulla» on a scheduled campaign, «Annulla» alone once it is
 *  sending, and «Invio fermo» with its reason when the send stopped (REB-524). The
 *  query refetches every 10s while the state is `scheduled` or `sending` and for
 *  five minutes after it was sent (`refetchEvery`), so a send in progress and the
 *  deliveries after it fill in on their own without a manual reload. */
export function AdminCampaign() {
  const { id } = useParams({ from: '/signedIn/admin/campaigns/$id' })
  const client = useQueryClient()
  const detail = useQuery({
    queryKey: keys.campaign(id),
    queryFn: () => admin.campaign(id),
    refetchInterval: (query) => {
      const campaign = query.state.data?.campaign
      return campaign ? refetchEvery(campaign) : false
    },
  })
  const invalidate = () => void client.invalidateQueries({ queryKey: keys.campaign(id) })
  const toDraft = useMutation({
    mutationFn: () => admin.campaignToDraft(id),
    onSuccess: invalidate,
  })
  const cancel = useMutation({
    mutationFn: () => admin.cancelCampaign(id),
    onSuccess: invalidate,
  })
  const navigate = useNavigate()
  const remove = useMutation({
    mutationFn: () => admin.deleteCampaign(id),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.campaigns })
      void navigate({ to: '/admin/campaigns' })
    },
  })
  const [filter, setFilter] = useState<Filter>('all')
  const followUp = useMutation({
    mutationFn: () => admin.followUpCampaign(id),
    onSuccess: (draft) => void navigate({ to: '/admin/campaigns/$id/edit', params: { id: draft.id } }),
  })

  if (detail.isError) return <Empty>Campagna non trovata.</Empty>
  if (detail.isPending) return <Empty>Caricamento…</Empty>

  const { campaign, counts, recipients } = detail.data
  const stall = stallLine(campaign)
  const moment = stall ? null : campaignMoment(campaign)
  const waiting = recipients.filter(didNothing).length
  const toDraftFailure =
    toDraft.error instanceof ApiError
      ? toDraft.error.message
      : toDraft.error
        ? 'Non riesco a riportare la campagna in bozza.'
        : null
  const cancelFailure =
    cancel.error instanceof ApiError
      ? cancel.error.message
      : cancel.error
        ? 'Non riesco ad annullare la campagna.'
        : null
  const followUpFailure =
    followUp.error instanceof ApiError
      ? followUp.error.message
      : followUp.error
        ? 'Non riesco a preparare la bozza.'
        : null
  const removeFailure =
    remove.error instanceof ApiError ? remove.error.message : remove.error ? 'Non riesco a eliminare la bozza.' : null

  return (
    <>
      <Header title={campaign.name}>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="pill" dot={isStalled(campaign) ? 'danger' : undefined}>
            {campaignStateLabel(campaign)}
          </Badge>
          {campaign.status === 'sent' && waiting > 0 && (
            <Button type="button" size="sm" onClick={() => followUp.mutate()} disabled={followUp.isPending}>
              {followUp.isPending ? 'Preparo la bozza…' : `Riscrivi a chi non ha fatto niente (${waiting})`}
            </Button>
          )}
          {campaign.status === 'draft' && (
            <Button asChild variant="outline" size="sm">
              <Link to="/admin/campaigns/$id/edit" params={{ id: campaign.id }}>
                Modifica
              </Link>
            </Button>
          )}
          {campaign.status === 'draft' && (
            <ConfirmAction
              label="Elimina"
              question="Eliminare la bozza?"
              pendingLabel="Elimino…"
              pending={remove.isPending}
              onConfirm={() => remove.mutate()}
              variant="destructive"
            />
          )}
          {campaign.status === 'scheduled' && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => toDraft.mutate()}
              disabled={toDraft.isPending}
            >
              {toDraft.isPending ? 'Riporto in bozza…' : 'Riporta in bozza'}
            </Button>
          )}
          {(campaign.status === 'scheduled' || campaign.status === 'sending') && (
            <ConfirmAction
              label="Annulla"
              question="Annullare l’invio?"
              pendingLabel="Annullo…"
              pending={cancel.isPending}
              onConfirm={() => cancel.mutate()}
              variant="destructive"
            />
          )}
        </div>
      </Header>
      {moment && <p className="px-6 pt-4 text-sm text-muted-foreground">{moment}</p>}
      {stall && (
        <p role="status" className="px-6 pt-4 text-sm text-destructive">
          {stall}
        </p>
      )}
      {campaign.follows_id && (
        <p className="px-6 pt-2 text-sm text-muted-foreground">
          Riscrive a chi non aveva fatto niente dopo{' '}
          <Link to="/admin/campaigns/$id" params={{ id: campaign.follows_id }} className="underline">
            un'altra campagna
          </Link>
          .
        </p>
      )}
      {(toDraftFailure || cancelFailure || followUpFailure || removeFailure) && (
        <p role="alert" className="px-6 pt-4 text-sm text-destructive">
          {toDraftFailure ?? cancelFailure ?? followUpFailure ?? removeFailure}
        </p>
      )}
      <dl className="grid grid-cols-2 gap-6 border-b px-6 py-6 sm:grid-cols-3 lg:grid-cols-5">
        <Figure label="Inviate" value={counts.sent} note={`su ${counts.recipients}`} />
        <Figure label="Consegnate" value={counts.delivered} note={share(counts.delivered, counts.sent)} />
        <Figure label="Cliccate" value={counts.clicked} note={share(counts.clicked, counts.sent)} />
        <Figure label="Entrate nell’area" value={counts.income} note={share(counts.income, counts.sent)} />
        <Figure label={ACTION_DONE_LABELS[campaign.action]} value={counts.actions} note={share(counts.actions, counts.sent)} />
        <Figure label="Rimbalzate" value={counts.bounced} note={share(counts.bounced, counts.sent)} />
        <Figure label="Saltate" value={counts.skipped} note={share(counts.skipped, counts.recipients)} />
        <Figure label="Fallite" value={counts.failed} note={share(counts.failed, counts.recipients)} />
        {counts.queued > 0 && <Figure label="In coda" value={counts.queued} />}
      </dl>
      {recipients.length === 0 ? (
        <Empty>Nessun destinatario.</Empty>
      ) : (
        <div className="px-6 py-6">
          <div className="mb-4 flex flex-wrap gap-2" role="group" aria-label="Chi mostrare">
            {FILTERS.map(([value, label]) => (
              <Button
                key={value}
                type="button"
                size="sm"
                variant={filter === value ? 'default' : 'outline'}
                aria-pressed={filter === value}
                onClick={() => setFilter(value)}
              >
                {label} ({recipients.filter((r) => matches(r, value)).length})
              </Button>
            ))}
          </div>
          <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
            <Table scrollLabel="Persone della campagna">
              <TableHeader>
                <TableRow>
                  <TableHead>Persona</TableHead>
                  <TableHead>Stato</TableHead>
                  <TableHead>Consegna</TableHead>
                  <TableHead>Clic</TableHead>
                  <TableHead>Entrata</TableHead>
                  <TableHead>Azione</TableHead>
                  <TableHead className="w-40"><span className="sr-only">Azioni</span></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {recipients.filter((r) => matches(r, filter)).map((recipient) => (
                  <RecipientRow key={recipient.id} recipient={recipient} />
                ))}
              </TableBody>
            </Table>
          </div>
        </div>
      )}
    </>
  )
}
