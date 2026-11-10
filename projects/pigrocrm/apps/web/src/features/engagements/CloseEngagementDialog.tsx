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
import { useChangeEngagementState } from './queries'

/**
 * «Chiudi l'incarico…» asks the day it ended (it starts on today); «Annulla l'incarico…», offered before
 * the start date, asks for nothing but a confirmation: the engagement never started, and its letter keeps
 * its number (design § 1). Neither touches a month already decided or a payment: they stay.
 */
export function CloseEngagementDialog({
  engagementId,
  mode,
  onClose,
}: {
  engagementId: string
  mode: 'conclude' | 'cancel'
  onClose: () => void
}) {
  const change = useChangeEngagementState(engagementId)
  const [endedOn, setEndedOn] = useState(() => toIsoDate(new Date()))
  const concluding = mode === 'conclude'
  return (
    <Dialog open onOpenChange={(next) => !next && !change.isPending && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{concluding ? "Chiudi l'incarico" : "Annulla l'incarico"}</DialogTitle>
          <DialogDescription>
            {concluding
              ? 'Scrivi il giorno in cui finisce. I mesi già decisi e i pagamenti restano dove sono.'
              : "L'incarico non è ancora partito: resta nell'elenco come annullato e la lettera tiene il suo numero."}
          </DialogDescription>
        </DialogHeader>
        {change.isError ? <QueryErrorBanner error={change.error} /> : null}
        {concluding ? (
          <div className="space-y-2">
            <Label htmlFor="engagement-ended-on">Finito il</Label>
            <Input
              id="engagement-ended-on"
              type="date"
              value={endedOn}
              onChange={(event) => setEndedOn(event.target.value)}
            />
          </div>
        ) : null}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={change.isPending}>
            Indietro
          </Button>
          <Button
            disabled={change.isPending || (concluding && endedOn === '')}
            onClick={() =>
              change.mutate(
                concluding ? { state: 'concluded', ended_on: endedOn } : { state: 'cancelled' },
                {
                  onSuccess: () => {
                    toast.success(concluding ? 'Incarico chiuso.' : 'Incarico annullato.')
                    onClose()
                  },
                },
              )
            }
          >
            {change.isPending ? <Loader className="mr-2 size-4" /> : null}
            {concluding ? "Chiudi l'incarico" : "Annulla l'incarico"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
