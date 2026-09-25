import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from '@tanstack/react-router'
import { useState } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin, ApiError, type CampaignRecipient } from '@/lib/api'
import { CAMPAIGN_STATE_LABELS, RECIPIENT_STATE_LABELS } from '@/lib/campaigns'
import { formatDateTime } from '@/lib/format'
import { Empty, Figure, Header } from './lists'

/** A row's own «Non scrivere mai» (REB-473): one click, no confirmation -- the same
 *  reasoning `RecordLifecycle` already gives, since the opt-out is itself reversible
 *  only by the person writing back in. The row keeps `email` after success, so the
 *  page has something to render the fixed sentence beside. */
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
      <Button type="button" variant="outline" size="sm" onClick={() => neverWrite.mutate()} disabled={neverWrite.isPending}>
        {neverWrite.isPending ? 'Registro…' : 'Non scrivere mai'}
      </Button>
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

/** «Annulla», with an inline second click in place of a browser `confirm()`: the first
 *  click swaps the button for the question, the second one runs the mutation. */
function CancelAction({ pending, onConfirm }: { pending: boolean; onConfirm: () => void }) {
  const [asking, setAsking] = useState(false)
  if (!asking) {
    return (
      <Button type="button" variant="destructive" size="sm" onClick={() => setAsking(true)}>
        Annulla
      </Button>
    )
  }
  return (
    <div className="flex items-center gap-2">
      <span className="text-sm">Annullare l’invio?</span>
      <Button
        type="button"
        variant="destructive"
        size="sm"
        disabled={pending}
        onClick={() => onConfirm()}
      >
        {pending ? 'Annullo…' : 'Conferma'}
      </Button>
      <Button type="button" variant="outline" size="sm" onClick={() => setAsking(false)} disabled={pending}>
        Indietro
      </Button>
    </div>
  )
}

/** «Campagna» (P-REB-41): the numbers, the recipients and what an admin can still undo
 *  from here -- «Modifica» on a draft, «Riporta in bozza»/«Annulla» on a scheduled
 *  campaign, «Annulla» alone once it is sending. The query refetches every 10s while
 *  the state is `programmata` or `in_invio`, so a send in progress fills in on its
 *  own without a manual reload. */
export function AdminCampagna() {
  const { id } = useParams({ from: '/signedIn/admin/campaigns/$id' })
  const client = useQueryClient()
  const detail = useQuery({
    queryKey: ['campaign', id],
    queryFn: () => admin.campaign(id),
    refetchInterval: (query) => {
      const stato = query.state.data?.campagna.stato
      return stato === 'programmata' || stato === 'in_invio' ? 10_000 : false
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
            <CancelAction pending={cancel.isPending} onConfirm={() => cancel.mutate()} />
          )}
        </div>
      </Header>
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
