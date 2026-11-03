import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Textarea } from '@rebase/ui/textarea'
import { toProblem, type ProblemDetail } from '@/lib/api'
import { toIsoDate } from '@/lib/dates'
import {
  useAnnulInvoice,
  useMarkTransmitted,
  useSetPaymentState,
} from './queries'

/**
 * The three dialogs of the invoice action bar: record a collection, record a transmission,
 * annul. Each is controlled by the bar (the buttons that open them live there) and reports
 * a refusal through `onProblem`, into the banner every other action of the bar uses.
 * `payment` and `transmitted` are the bar's own mutations, since its buttons read their
 * `isPending` too.
 */
interface DialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onProblem: (problem: ProblemDetail | null) => void
}

// The two dates default to today, read from local parts (`toIsoDate`): late in the
// evening the UTC route would propose tomorrow, which the server refuses.
export function CollectDialog({
  payment,
  open,
  onOpenChange,
  onProblem,
}: DialogProps & { payment: ReturnType<typeof useSetPaymentState> }) {
  const [paymentDate, setPaymentDate] = useState(() => toIsoDate(new Date()))

  function onCollect() {
    onProblem(null)
    payment.mutate(
      { payment_status: 'paid', payment_date: paymentDate },
      {
        onSuccess: () => {
          toast.success('Incasso registrato')
          onOpenChange(false)
        },
        onError: (error) => onProblem(toProblem(error)),
      },
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Registra l&apos;incasso</DialogTitle>
        </DialogHeader>
        {/* The date is required by the server («un incasso senza data non è un
            incasso»): the day the money arrived is what the cash view and the fiscal
            estimate read, so it is asked for here rather than assumed to be today. */}
        <p className="text-muted-foreground text-sm">
          La fattura passa a «Incassato» e la data entra nella vista economica dell&apos;anno
          in cui cade. Si può tornare indietro.
        </p>
        <div className="space-y-2">
          <Label htmlFor="data-incasso">Data incasso</Label>
          <Input
            id="data-incasso"
            type="date"
            required
            value={paymentDate}
            onChange={(event) => setPaymentDate(event.target.value)}
          />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Chiudi
          </Button>
          <Button onClick={onCollect} disabled={payment.isPending || paymentDate === ''}>
            Registra incasso
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function TransmitDialog({
  transmitted,
  open,
  onOpenChange,
  onProblem,
}: DialogProps & { transmitted: ReturnType<typeof useMarkTransmitted> }) {
  const [dateTransmission, setDateTransmission] = useState(() => toIsoDate(new Date()))

  function onTransmit() {
    onProblem(null)
    transmitted.mutate(dateTransmission, {
      onSuccess: () => {
        toast.success('Trasmissione registrata')
        onOpenChange(false)
      },
      onError: (error) => onProblem(toProblem(error)),
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Segna come trasmessa</DialogTitle>
        </DialogHeader>
        {/* Once. This is the column that tells an invoice that never left -- still
            annullable -- from one already deposited with the Agenzia delle Entrate,
            and it stays frozen for exactly that reason (`mark_transmitted_externally`). */}
        <p className="text-muted-foreground text-sm">
          Registra che l&apos;XML è stato consegnato all&apos;intermediario o allo SDI fuori da
          PigroCRM. Non si può annullare.
        </p>
        <div className="space-y-2">
          <Label htmlFor="data-trasmissione">Data di trasmissione</Label>
          <Input
            id="data-trasmissione"
            type="date"
            required
            value={dateTransmission}
            onChange={(event) => setDateTransmission(event.target.value)}
          />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Chiudi
          </Button>
          <Button
            onClick={onTransmit}
            disabled={transmitted.isPending || dateTransmission === ''}
          >
            Segna trasmessa
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function AnnulDialog({
  invoiceId,
  open,
  onOpenChange,
  onProblem,
}: DialogProps & { invoiceId: string }) {
  const [reason, setReason] = useState('')
  const annul = useAnnulInvoice(invoiceId)

  function onAnnul() {
    onProblem(null)
    annul.mutate(reason, {
      onSuccess: () => {
        toast.success('Documento annullato. Il numero resta nel registro.')
        onOpenChange(false)
      },
      onError: (error) => onProblem(toProblem(error)),
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Annulla documento</DialogTitle>
        </DialogHeader>
        {/* Annulment is not deletion: the number stays in the register and the row
            stays readable, because a fiscal register with a hole in it is a problem
            with the tax authority. The reason is required for the same purpose --
            it is what the document's own history will say later. */}
        <p className="text-muted-foreground text-sm">
          Il numero resta nel registro e il documento resta consultabile. Per correggere
          un errore si emette un nuovo documento, non si modifica questo.
        </p>
        <div className="space-y-2">
          <Label htmlFor="motivo-annullamento">Motivo</Label>
          <Textarea
            id="motivo-annullamento"
            value={reason}
            rows={3}
            onChange={(event) => setReason(event.target.value)}
          />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Chiudi
          </Button>
          <Button variant="destructive" onClick={onAnnul} disabled={annul.isPending}>
            Annulla documento
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
