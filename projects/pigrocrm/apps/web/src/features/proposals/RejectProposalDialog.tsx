import { useState } from 'react'
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
import { Textarea } from '@rebase/ui/textarea'
import { useRejectProposal } from './queries'

/**
 * «Rifiuta la proposta»: the one decision component for a refusal, drawn wherever a proposal can
 * be refused (the proposal's own page, and a person's panel on «Persone dell'azienda»), so the
 * same call answers with the same words. Nothing is created: the optional reason stays on the
 * proposal's timeline. What the server refuses is shown inside the dialog it came from.
 */
export function RejectProposalDialog({
  proposalId,
  open,
  onOpenChange,
  onRejected,
  description = 'Non viene creato niente. Il motivo resta nella cronologia della proposta, per chi la rivede dopo.',
  placeholder = 'Per esempio: la data non è quella della mail.',
}: {
  proposalId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Called once the server has recorded the refusal. */
  onRejected: () => void
  description?: string
  placeholder?: string
}) {
  const reject = useRejectProposal(proposalId)
  const [reason, setReason] = useState('')

  const onReject = () => {
    reject.mutate(
      { reason: reason.trim() === '' ? null : reason.trim() },
      {
        onSuccess: () => {
          onOpenChange(false)
          onRejected()
        },
      },
    )
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (reject.isPending) return
        // A refusal belongs to the attempt that met it: leaving the dialog forgets it.
        if (!next) reject.reset()
        onOpenChange(next)
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Rifiuta la proposta</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="proposal-reason">Motivo (facoltativo)</Label>
          <Textarea
            id="proposal-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder={placeholder}
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
              onOpenChange(false)
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
  )
}
