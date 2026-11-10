import { useState } from 'react'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { toIsoDate } from '@/lib/dates'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Loader } from '@rebase/ui/loader'
import { toast } from '@rebase/ui/sonner'
import { formatDate, monthLabel } from './labels'
import { monthStart } from './monthRows'
import { useRecordMonthlyReport } from './queries'

/**
 * «Registra l'arrivo»: rebase received the freelancer's «Rendiconto» of a month. The date is typed
 * and starts on today, so a late entry keeps the true day (the brief, question 5 B): it is the day
 * the server counts the seven working days from, and the server answers the tacit date.
 */
export function RecordArrivalDialog({
  engagementId,
  month,
  onClose,
}: {
  engagementId: string
  /** `2026-10`, the month whose report arrived. */
  month: string
  onClose: () => void
}) {
  const record = useRecordMonthlyReport(engagementId)
  const [receivedOn, setReceivedOn] = useState(() => toIsoDate(new Date()))
  return (
    <Dialog open onOpenChange={(next) => !next && !record.isPending && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Registra l'arrivo del rendiconto</DialogTitle>
          <DialogDescription>
            Il rendiconto di {monthLabel(monthStart(month))}: scrivi il giorno in cui è arrivato a
            rebase. Da quel giorno corrono i giorni per un'obiezione.
          </DialogDescription>
        </DialogHeader>
        {record.isError ? <QueryErrorBanner error={record.error} /> : null}
        <div className="space-y-2">
          <Label htmlFor="arrival-date">Arrivato il</Label>
          <Input
            id="arrival-date"
            type="date"
            value={receivedOn}
            onChange={(event) => setReceivedOn(event.target.value)}
          />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={record.isPending}>
            Annulla
          </Button>
          <Button
            disabled={record.isPending || receivedOn === ''}
            onClick={() =>
              record.mutate(
                { month, received_on: receivedOn },
                {
                  onSuccess: (saved) => {
                    toast.success(
                      `Arrivo registrato. Nessuna obiezione entro il ${formatDate(saved.tacit_on)}.`,
                    )
                    onClose()
                  },
                },
              )
            }
          >
            {record.isPending ? <Loader className="mr-2 size-4" /> : null}
            Registra l'arrivo
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
