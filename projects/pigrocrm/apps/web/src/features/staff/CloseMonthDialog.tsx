import { Lock } from 'lucide-react'
import { Button } from '@rebase/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@rebase/ui/dialog'
import { toast } from '@rebase/ui/sonner'
import { toProblem } from '@/lib/api'
import { monthName, namesList, stateOf } from './labels'
import { useCloseMonth, type MemberMonth } from './queries'

/**
 * «Chiudi il mese»: asks first and names who is still «Stimato», since closing locks the accepted
 * payslips and the estimates alike (staff design § 4). The server answers a month that has not
 * ended (422) or is already closed (409) in its own words, shown here.
 */
export function CloseMonthDialog({
  open,
  onOpenChange,
  legalEntityId,
  monthKey,
  members,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  legalEntityId: string
  /** The month worked, `YYYY-MM`. */
  monthKey: string
  members: MemberMonth[]
}) {
  const close = useCloseMonth(legalEntityId)
  const estimated = members.filter((row) => stateOf(row) === 'estimated').map((row) => row.name)
  const received = members.filter((row) => stateOf(row) === 'received').map((row) => row.name)

  return (
    <Dialog open={open} onOpenChange={(next) => !close.isPending && onOpenChange(next)}>
      <DialogContent className="gap-4 sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Chiudi il mese di {monthName(monthKey)}</DialogTitle>
          <DialogDescription>
            Chiudere blocca i cedolini accettati e le stime di questo mese: non potrai più registrarne né correggerne uno.
          </DialogDescription>
        </DialogHeader>
        {estimated.length === 0 && received.length === 0 ? (
          <p className="text-sm">Ogni persona ha il cedolino accettato.</p>
        ) : (
          <ul className="space-y-2 text-sm">
            {estimated.length === 0 ? null : (
              <li>
                Sono ancora «Stimato» e il mese si chiude con la stima: <strong>{namesList(estimated)}</strong>.
              </li>
            )}
            {received.length === 0 ? null : (
              <li>
                Hanno un cedolino «Ricevuto» che nessuno ha accettato: <strong>{namesList(received)}</strong>. Accettalo o
                rifiutalo prima di chiudere.
              </li>
            )}
          </ul>
        )}
        {close.error === null ? null : (
          <p role="alert" className="border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {toProblem(close.error).detail}
          </p>
        )}
        <DialogFooter>
          <Button type="button" variant="outline" disabled={close.isPending} onClick={() => onOpenChange(false)}>
            Annulla
          </Button>
          <Button
            type="button"
            disabled={close.isPending}
            onClick={() =>
              close.mutate(monthKey, {
                onSuccess: () => {
                  toast.success(`${monthName(monthKey)} chiuso`)
                  onOpenChange(false)
                },
              })
            }
          >
            <Lock aria-hidden className="mr-2 size-4" />
            {close.isPending ? 'Chiudo…' : 'Chiudi il mese'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
