import { useState } from 'react'
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
import { NOT_WRITTEN_SENTENCE, TAX_LABELS, type Tax } from './labels'
import { failureText, useSetInstalmentPlan } from './queries'

/**
 * «Rateizza giugno»: in how many monthly instalments the June balance of a tax year and the
 * first advance of the next one are paid. One instalment is a single payment and removes the
 * plan. How many the pack allows is the server's answer, said in its own sentence when it
 * refuses; a plan with a payment on one of its instalments is refused too.
 */
export function InstalmentPlanDialog({
  legalEntityId,
  year,
  tax,
  current,
  onClose,
}: {
  legalEntityId: string
  year: number
  tax: Tax
  /** How many instalments the plan has now; absent when there is none. */
  current: number | undefined
  onClose: () => void
}) {
  const [count, setCount] = useState(String(current ?? 2))
  const [problem, setProblem] = useState<string | null>(null)
  const [skipped, setSkipped] = useState<number | null>(null)
  const save = useSetInstalmentPlan(legalEntityId)
  // The count the person typed, not a figure of the API's: whole and from one up, as the field says.
  const valid = /^[1-9]\d*$/.test(count.trim())
  const instalments = Number.parseInt(count, 10)
  function submit() {
    if (!valid) {
      setProblem('Scrivi in quante rate, con un numero intero da 1 in su.')
      return
    }
    setProblem(null)
    save.mutate(
      { year, tax: tax as 'irpef' | 'substitute_tax' | 'ires' | 'irap', instalments },
      {
        onSuccess: (done) => {
          const left = done.items.not_written ?? []
          if (left.length === 0) onClose()
          else setSkipped(left.length)
        },
        onError: (error) => setProblem(failureText(error)),
      },
    )
  }
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            Rateizza {TAX_LABELS[tax]} {year}
          </DialogTitle>
          <DialogDescription>
            Il saldo di giugno e il primo acconto dell’anno dopo si pagano a rate mensili. L’acconto di
            novembre non si divide. Una rata sola toglie il piano.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="instalment-count">Numero di rate</Label>
          <Input
            id="instalment-count"
            type="number"
            min={1}
            inputMode="numeric"
            value={count}
            onChange={(event) => setCount(event.target.value)}
            className="tabular-nums"
          />
        </div>
        {problem ? <p role="alert" className="text-sm text-destructive">{problem}</p> : null}
        {skipped !== null ? <p role="status" className="text-sm">{NOT_WRITTEN_SENTENCE(skipped)}</p> : null}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {skipped === null ? 'Annulla' : 'Chiudi'}
          </Button>
          {skipped === null ? (
            <Button onClick={submit} disabled={save.isPending}>
              Salva la rateazione
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
