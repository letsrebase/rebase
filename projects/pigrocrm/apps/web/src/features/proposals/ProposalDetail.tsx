import { Link, useNavigate } from '@tanstack/react-router'
import { Check, FileText, Inbox, X } from 'lucide-react'
import { useState } from 'react'
import { PageHeader } from '@/components/PageHeader'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Label } from '@rebase/ui/label'
import { Loader } from '@rebase/ui/loader'
import { toast } from '@rebase/ui/sonner'
import { Textarea } from '@rebase/ui/textarea'
import { toProblem } from '@/lib/api'
import { useAuth, useCanWrite } from '@/lib/auth'
import { Confidence, ProposalStatoPill, ProposalSubject } from './ProposalBits'
import { decisionLine, describeProposal, type Proposal } from './fields'
import { useAcceptProposal, useProposal, useProposalTimeline, useRejectProposal } from './queries'

/**
 * One proposal, with everything a reviewer needs before deciding next to the buttons that
 * decide: where it came from (the verbatim excerpt and a way into the archived document),
 * how sure the agent was and why, and every field it proposes, worded as the contract or
 * the day it would become.
 *
 * Accepting builds the proposal exactly as proposed: editing a field first is the
 * `campi_accettati` of the API and is deliberately not offered here, because a correction
 * is a different decision from «yes» and the agent can be asked again. Rejecting takes a
 * reason, which the API keeps on the proposal's timeline.
 *
 * What the server refuses (a customer that is gone, a day that already exists on that
 * date, a rate card that overlaps another) is shown in the server's own words above the
 * buttons, and the proposal stays in the queue: nothing was written.
 */
export function ProposalDetail({ proposalId }: { proposalId: string }) {
  const proposal = useProposal(proposalId)
  if (proposal.isError) {
    if (toProblem(proposal.error).status === 404) return <p className="p-8">Proposta non trovata.</p>
    return (
      <div className="p-8">
        <QueryErrorBanner error={proposal.error} />
      </div>
    )
  }
  if (!proposal.data) {
    return (
      <div role="status" className="flex items-center gap-2 p-8 text-muted-foreground">
        <Loader className="size-4" />
        Caricamento…
      </div>
    )
  }
  return <Loaded proposal={proposal.data} />
}

function Loaded({ proposal }: { proposal: Proposal }) {
  const description = describeProposal(proposal)
  const canWrite = useCanWrite()
  const { user } = useAuth()
  const navigate = useNavigate()
  const accept = useAcceptProposal(proposal.id)
  const reject = useRejectProposal(proposal.id)
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState('')
  const pending = proposal.stato === 'in_attesa'
  // The decision is recorded against a name a person reads, not a user id: `deciso_da` is
  // free text on the proposal and shows in the decided list. `||`, not `??`: an empty name
  // would be refused by the API as a decision nobody made.
  const decidedBy = user?.nome || user?.email || ''

  const backToQueue = () => void navigate({ to: '/app/proposals', search: { stato: 'in_attesa' } })

  const onAccept = () => {
    accept.mutate(
      { deciso_da: decidedBy },
      {
        onSuccess: () => {
          toast.success(
            proposal.target_type === 'contratto'
              ? 'Proposta accettata: il contratto è stato creato'
              : 'Proposta accettata: la giornata è registrata',
          )
          backToQueue()
        },
      },
    )
  }

  const onReject = () => {
    reject.mutate(
      { deciso_da: decidedBy, motivo: reason.trim() === '' ? null : reason.trim() },
      {
        onSuccess: () => {
          setRejecting(false)
          toast.success('Proposta rifiutata')
          backToQueue()
        },
      },
    )
  }

  // Only the accept failure is the page's: a refused rejection is shown inside the dialog
  // it came from, and showing it twice (or after Annulla) would be a stale alert.
  const failure = accept.error

  return (
    <>
      <PageHeader
        icon={Inbox}
        title={description.title}
        description={
          proposal.target_type === 'contratto'
            ? 'Proposta di nuovo contratto'
            : 'Proposta di giornata lavorata'
        }
        actions={
          pending && canWrite ? (
            <>
              <Button
                variant="outline"
                disabled={accept.isPending || reject.isPending}
                onClick={() => {
                  reject.reset()
                  setRejecting(true)
                }}
              >
                <X className="mr-2 size-4" aria-hidden />
                Rifiuta
              </Button>
              <Button disabled={accept.isPending || reject.isPending} onClick={onAccept}>
                {accept.isPending ? <Loader className="mr-2 size-4" /> : <Check className="mr-2 size-4" aria-hidden />}
                Accetta
              </Button>
            </>
          ) : undefined
        }
      />

      <div className="max-w-4xl space-y-8 px-8 pb-10">
        <p>
          <Link to="/app/proposals" search={{ stato: 'in_attesa' }} className="text-sm text-muted-foreground underline underline-offset-4">
            Tutte le proposte
          </Link>
        </p>

        {failure ? (
          <div role="alert" className="border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            <p className="font-medium">Non è stato possibile accettare la proposta.</p>
            <p>{toProblem(failure).detail}</p>
          </div>
        ) : null}

        {pending ? null : <Outcome proposal={proposal} customerId={description.customerId} />}

        <section aria-labelledby="proposta-origine" className="space-y-3">
          <h2 id="proposta-origine" className="font-semibold">
            Da dove arriva
          </h2>
          <blockquote className="whitespace-pre-line border-l-2 border-foreground pl-4 text-sm">
            {proposal.estratto}
          </blockquote>
          <p className="text-xs text-muted-foreground">
            {proposal.tipo_estratto === 'trascritto'
              ? 'Trascritto da una pagina senza testo: non è verificabile sul documento.'
              : 'Passo citato dal testo del documento.'}
          </p>
          <p className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
            <Confidence proposal={proposal} showReason />
          </p>
          <Button asChild variant="outline" size="sm">
            <Link to="/app/documents/$documentId" params={{ documentId: proposal.document_id }}>
              <FileText className="mr-2 size-4" aria-hidden />
              Apri il documento
            </Link>
          </Button>
        </section>

        <section aria-labelledby="proposta-campi" className="space-y-6">
          <div className="space-y-1">
            <h2 id="proposta-campi" className="font-semibold">
              Cosa propone
            </h2>
            <p className="text-sm text-muted-foreground">
              <ProposalSubject
                description={description}
                link
                prefix={proposal.target_type === 'contratto' ? 'Per il cliente ' : 'Sul contratto '}
              />
            </p>
          </div>
          {description.groups.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Questa proposta non ha campi che la schermata sappia leggere. Accettarla o rifiutarla resta possibile.
            </p>
          ) : (
            description.groups.map((group) => (
              <div key={group.title} className="space-y-1">
                <h3 className="text-sm font-medium text-muted-foreground">{group.title}</h3>
                <dl className="border-t">
                  {group.rows.map((row) => (
                    <div key={row.label} className="flex justify-between gap-6 border-b py-2 text-sm">
                      <dt className="text-muted-foreground">{row.label}</dt>
                      <dd className="text-right font-medium">{row.value}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            ))
          )}
        </section>
      </div>

      <Dialog
        open={rejecting}
        onOpenChange={(open) => {
          if (reject.isPending) return
          // A refusal belongs to the attempt that met it: leaving the dialog forgets it.
          if (!open) reject.reset()
          setRejecting(open)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Rifiuta la proposta</DialogTitle>
            <DialogDescription>
              Non viene creato niente. Il motivo resta nella cronologia della proposta, per chi la rivede dopo.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="proposta-motivo">Motivo (facoltativo)</Label>
            <Textarea
              id="proposta-motivo"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Per esempio: la data non è quella della mail."
              rows={3}
            />
          </div>
          {reject.error ? <QueryErrorBanner error={reject.error} /> : null}
          <DialogFooter>
            <Button
              variant="outline"
              disabled={reject.isPending}
              onClick={() => {
                reject.reset()
                setRejecting(false)
              }}
            >
              Annulla
            </Button>
            <Button variant="destructive" disabled={reject.isPending} onClick={onReject}>
              {reject.isPending ? <Loader className="mr-2 size-4" /> : null}
              Rifiuta la proposta
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

/**
 * What happened to a decided proposal, and what it produced. A rejection's reason is not on
 * the proposal: the API keeps it on the `rejected` row of the proposal's timeline, so a
 * rejected proposal reads that once to say why.
 */
function Outcome({ proposal, customerId }: { proposal: Proposal; customerId: string | null }) {
  const accepted = proposal.stato === 'accettata'
  const timeline = useProposalTimeline(proposal.id, !accepted)
  const motivo = timeline.data?.find((entry) => entry.kind === 'rejected')?.payload.motivo
  return (
    <section aria-label="Esito" className="space-y-2 border bg-paper px-4 py-3 text-sm">
      <p className="flex flex-wrap items-center gap-2">
        <ProposalStatoPill stato={proposal.stato} />
        <span>{decisionLine(proposal)}</span>
      </p>
      {accepted ? (
        <p className="text-muted-foreground">
          {proposal.target_type === 'contratto'
            ? 'Ha creato il contratto, con la sua prima scheda tariffaria.'
            : 'Ha registrato la giornata come approvata.'}{' '}
          {customerId !== null ? (
            <Link
              to="/app/customers/$customerId"
              params={{ customerId }}
              className="text-foreground underline underline-offset-4"
            >
              Vai al cliente
            </Link>
          ) : null}
        </p>
      ) : typeof motivo === 'string' && motivo !== '' ? (
        <p>
          <span className="text-muted-foreground">Motivo: </span>
          {motivo}
        </p>
      ) : null}
    </section>
  )
}
