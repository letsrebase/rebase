import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from '@tanstack/react-router'
import { useState } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin, ApiError, type CampaignRecipient } from '@/lib/api'
import {
  AZIONE_FATTA_LABELS,
  RECIPIENT_STATE_LABELS,
  campaignMoment,
  campaignStateLabel,
  isStalled,
  refetchEvery,
  share,
  stallLine,
} from '@/lib/campaigns'
import { formatDateTime } from '@/lib/format'
import { Empty, Figure, Header } from './lists'

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

type Filtro = 'tutti' | 'azione' | 'niente'

const FILTERS: [Filtro, string][] = [
  ['tutti', 'Tutti'],
  ['azione', 'Ha fatto l’azione'],
  ['niente', 'Non ha fatto niente'],
]

/** Whom the mail reached with no action after it: the list «Riscrivi a chi non ha fatto
 *  niente» starts from (spec § 4.3). A bounced mail reached nobody and a complaint is a
 *  «never again», so neither counts, as the server's `waiting_rows` (REB-524). */
function didNothing(recipient: CampaignRecipient): boolean {
  return (
    recipient.stato === 'inviata' &&
    recipient.azione_at === null &&
    recipient.rimbalzata_at === null &&
    recipient.reclamo_at === null
  )
}

function matches(recipient: CampaignRecipient, filtro: Filtro): boolean {
  if (filtro === 'azione') return recipient.azione_at !== null
  if (filtro === 'niente') return didNothing(recipient)
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
        <p className="font-medium">{recipient.nome ?? '—'}</p>
        <p className="text-xs text-muted-foreground">{recipient.email}</p>
      </TableCell>
      <TableCell>
        <p>{RECIPIENT_STATE_LABELS[recipient.stato]}</p>
        {recipient.inviata_at && <p className="text-xs text-muted-foreground">{formatDateTime(recipient.inviata_at)}</p>}
        {recipient.motivo && <p className="text-xs text-muted-foreground">{recipient.motivo}</p>}
      </TableCell>
      <TableCell className="text-muted-foreground">
        {recipient.rimbalzata_at ? (
          <>
            <p className="text-destructive">Rimbalzata</p>
            <p className="text-xs">{formatDateTime(recipient.rimbalzata_at)}</p>
          </>
        ) : (
          <Moment at={recipient.consegnata_at} />
        )}
      </TableCell>
      <TableCell className="text-muted-foreground">
        <Moment at={recipient.primo_clic_at} />
      </TableCell>
      <TableCell className="text-muted-foreground">
        <Moment at={recipient.entrato_at} fromMail={recipient.entrato_dalla_mail} />
      </TableCell>
      <TableCell className="text-muted-foreground">
        <Moment at={recipient.azione_at} fromMail={recipient.azione_dalla_mail} />
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
 *  query refetches every 10s while the state is `programmata` or `in_invio` and for
 *  five minutes after it was sent (`refetchEvery`), so a send in progress and the
 *  deliveries after it fill in on their own without a manual reload. */
export function AdminCampagna() {
  const { id } = useParams({ from: '/signedIn/admin/campaigns/$id' })
  const client = useQueryClient()
  const detail = useQuery({
    queryKey: ['campaign', id],
    queryFn: () => admin.campaign(id),
    refetchInterval: (query) => {
      const campagna = query.state.data?.campagna
      return campagna ? refetchEvery(campagna) : false
    },
  })
  const invalidate = () => void client.invalidateQueries({ queryKey: ['campaign', id] })
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
      void client.invalidateQueries({ queryKey: ['campaigns'] })
      void navigate({ to: '/admin/campaigns' })
    },
  })
  const [filtro, setFiltro] = useState<Filtro>('tutti')
  const followUp = useMutation({
    mutationFn: () => admin.followUpCampaign(id),
    onSuccess: (draft) => void navigate({ to: '/admin/campaigns/$id/edit', params: { id: draft.id } }),
  })

  if (detail.isError) return <Empty>Campagna non trovata.</Empty>
  if (detail.isPending) return <Empty>Caricamento…</Empty>

  const { campagna, conteggi, destinatari } = detail.data
  const stall = stallLine(campagna)
  const moment = stall ? null : campaignMoment(campagna)
  const waiting = destinatari.filter(didNothing).length
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
      <Header title={campagna.nome}>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="pill" dot={isStalled(campagna) ? 'danger' : undefined}>
            {campaignStateLabel(campagna)}
          </Badge>
          {campagna.stato === 'inviata' && waiting > 0 && (
            <Button type="button" size="sm" onClick={() => followUp.mutate()} disabled={followUp.isPending}>
              {followUp.isPending ? 'Preparo la bozza…' : `Riscrivi a chi non ha fatto niente (${waiting})`}
            </Button>
          )}
          {campagna.stato === 'bozza' && (
            <Button asChild variant="outline" size="sm">
              <Link to="/admin/campaigns/$id/edit" params={{ id: campagna.id }}>
                Modifica
              </Link>
            </Button>
          )}
          {campagna.stato === 'bozza' && (
            <ConfirmAction
              label="Elimina"
              question="Eliminare la bozza?"
              pendingLabel="Elimino…"
              pending={remove.isPending}
              onConfirm={() => remove.mutate()}
              variant="destructive"
            />
          )}
          {campagna.stato === 'programmata' && (
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
          {(campagna.stato === 'programmata' || campagna.stato === 'in_invio') && (
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
      {campagna.segue_id && (
        <p className="px-6 pt-2 text-sm text-muted-foreground">
          Riscrive a chi non aveva fatto niente dopo{' '}
          <Link to="/admin/campaigns/$id" params={{ id: campagna.segue_id }} className="underline">
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
        <Figure label="Inviate" value={conteggi.inviate} note={`su ${conteggi.destinatari}`} />
        <Figure label="Consegnate" value={conteggi.consegnate} note={share(conteggi.consegnate, conteggi.inviate)} />
        <Figure label="Cliccate" value={conteggi.cliccate} note={share(conteggi.cliccate, conteggi.inviate)} />
        <Figure label="Entrate nell’area" value={conteggi.entrate} note={share(conteggi.entrate, conteggi.inviate)} />
        <Figure label={AZIONE_FATTA_LABELS[campagna.azione]} value={conteggi.azioni} note={share(conteggi.azioni, conteggi.inviate)} />
        <Figure label="Rimbalzate" value={conteggi.rimbalzate} note={share(conteggi.rimbalzate, conteggi.inviate)} />
        <Figure label="Saltate" value={conteggi.saltate} note={share(conteggi.saltate, conteggi.destinatari)} />
        <Figure label="Fallite" value={conteggi.fallite} note={share(conteggi.fallite, conteggi.destinatari)} />
        {conteggi.in_coda > 0 && <Figure label="In coda" value={conteggi.in_coda} />}
      </dl>
      {destinatari.length === 0 ? (
        <Empty>Nessun destinatario.</Empty>
      ) : (
        <div className="px-6 py-6">
          <div className="mb-4 flex flex-wrap gap-2" role="group" aria-label="Chi mostrare">
            {FILTERS.map(([value, label]) => (
              <Button
                key={value}
                type="button"
                size="sm"
                variant={filtro === value ? 'default' : 'outline'}
                aria-pressed={filtro === value}
                onClick={() => setFiltro(value)}
              >
                {label} ({destinatari.filter((r) => matches(r, value)).length})
              </Button>
            ))}
          </div>
          <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
            <Table>
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
                {destinatari.filter((r) => matches(r, filtro)).map((recipient) => (
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
