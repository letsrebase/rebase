import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from '@tanstack/react-router'
import { useState } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin, ApiError, type CampaignRecipient } from '@/lib/api'
import { CAMPAIGN_STATE_LABELS, RECIPIENT_STATE_LABELS, campaignMoment, refetchEvery } from '@/lib/campaigns'
import { formatDateTime } from '@/lib/format'
import { Empty, Figure, Header } from './lists'

/** An action behind an inline second click, in place of a browser `confirm()`: the
 *  first click swaps the button for the question, the second one runs it. «Annulla»
 *  and «Non scrivere mai» both use it -- neither can be undone from this page. */
function ConfirmAction({
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

function RecipientRow({ recipient }: { recipient: CampaignRecipient }) {
  return (
    <TableRow>
      <TableCell>
        <p className="font-medium">{recipient.nome ?? '—'}</p>
        <p className="text-xs text-muted-foreground">{recipient.email}</p>
      </TableCell>
      <TableCell>
        <p>{RECIPIENT_STATE_LABELS[recipient.stato]}</p>
        {recipient.motivo && <p className="text-xs text-muted-foreground">{recipient.motivo}</p>}
      </TableCell>
      <TableCell className="text-muted-foreground">
        {recipient.inviata_at ? formatDateTime(recipient.inviata_at) : ''}
      </TableCell>
      <TableCell className="text-muted-foreground">
        {recipient.consegnata_at ? formatDateTime(recipient.consegnata_at) : ''}
      </TableCell>
      <TableCell className="text-muted-foreground">
        {recipient.rimbalzata_at ? formatDateTime(recipient.rimbalzata_at) : ''}
      </TableCell>
      <TableCell>
        <NeverWriteCell email={recipient.email} />
      </TableCell>
    </TableRow>
  )
}

/** «Campagna» (P-REB-41): when it leaves or left, the numbers, the recipients and
 *  what an admin can still undo from here -- «Modifica» on a draft, «Riporta in
 *  bozza»/«Annulla» on a scheduled campaign, «Annulla» alone once it is sending. The
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

  if (detail.isError) return <Empty>Campagna non trovata.</Empty>
  if (detail.isPending) return <Empty>Caricamento…</Empty>

  const { campagna, conteggi, destinatari } = detail.data
  const moment = campaignMoment(campagna)
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

  return (
    <>
      <Header title={campagna.nome}>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="pill">{CAMPAIGN_STATE_LABELS[campagna.stato]}</Badge>
          {campagna.stato === 'bozza' && (
            <Button asChild variant="outline" size="sm">
              <Link to="/admin/campaigns/$id/edit" params={{ id: campagna.id }}>
                Modifica
              </Link>
            </Button>
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
      {(toDraftFailure || cancelFailure) && (
        <p role="alert" className="px-6 pt-4 text-sm text-destructive">
          {toDraftFailure ?? cancelFailure}
        </p>
      )}
      <dl className="grid grid-cols-2 gap-6 border-b px-6 py-6 sm:grid-cols-4 lg:grid-cols-7">
        <Figure label="Destinatari" value={conteggi.destinatari} />
        <Figure label="In coda" value={conteggi.in_coda} />
        <Figure label="Inviate" value={conteggi.inviate} />
        <Figure label="Consegnate" value={conteggi.consegnate} />
        <Figure label="Rimbalzate" value={conteggi.rimbalzate} />
        <Figure label="Saltate" value={conteggi.saltate} />
        <Figure label="Fallite" value={conteggi.fallite} />
      </dl>
      {destinatari.length === 0 ? (
        <Empty>Nessun destinatario.</Empty>
      ) : (
        <div className="px-6 py-6">
          <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Persona</TableHead>
                  <TableHead>Stato</TableHead>
                  <TableHead>Inviata</TableHead>
                  <TableHead>Consegnata</TableHead>
                  <TableHead>Rimbalzata</TableHead>
                  <TableHead className="w-40"><span className="sr-only">Azioni</span></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {destinatari.map((recipient) => (
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
