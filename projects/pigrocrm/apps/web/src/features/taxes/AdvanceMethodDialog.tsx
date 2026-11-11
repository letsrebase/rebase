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
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { ADVANCE_METHOD_LABELS, NOT_WRITTEN_SENTENCE, type AdvanceMethodName } from './labels'
import { failureText, useSetAdvanceMethod } from './queries'

/**
 * «Cambia il metodo dell’acconto»: the year's advances are computed on the previous year's tax
 * (`Storico`) or on this year's estimate (`Previsionale`). Choosing recomputes the entity's tax
 * due items; what it could not write is said in one sentence, the reason stays with the server.
 */
export function AdvanceMethodDialog({
  legalEntityId,
  year,
  current,
  onClose,
}: {
  legalEntityId: string
  year: number
  current: AdvanceMethodName
  onClose: () => void
}) {
  const [method, setMethod] = useState<AdvanceMethodName>(current)
  const [problem, setProblem] = useState<string | null>(null)
  const [skipped, setSkipped] = useState<number | null>(null)
  const save = useSetAdvanceMethod(legalEntityId)
  function submit() {
    setProblem(null)
    save.mutate(
      { year, method },
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
          <DialogTitle>Metodo dell’acconto {year}</DialogTitle>
          <DialogDescription>
            Come si calcolano gli acconti di questo anno d’imposta. Cambiarlo ricalcola le scadenze.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="advance-method">Metodo</Label>
          <Select value={method} onValueChange={(next) => setMethod(next as AdvanceMethodName)}>
            <SelectTrigger id="advance-method" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(Object.keys(ADVANCE_METHOD_LABELS) as AdvanceMethodName[]).map((name) => (
                <SelectItem key={name} value={name}>
                  {ADVANCE_METHOD_LABELS[name].label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-sm text-muted-foreground">{ADVANCE_METHOD_LABELS[method].hint}</p>
        </div>
        {problem ? <p role="alert" className="text-sm text-destructive">{problem}</p> : null}
        {skipped !== null ? <p role="status" className="text-sm">{NOT_WRITTEN_SENTENCE(skipped)}</p> : null}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {skipped === null ? 'Annulla' : 'Chiudi'}
          </Button>
          {skipped === null ? (
            <Button onClick={submit} disabled={save.isPending}>
              Salva il metodo
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
