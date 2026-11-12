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
import { LoadingState } from '@/components/LoadingState'
import { TAX_LABELS, type DeclaredFigure, type Tax } from './labels'
import { parseAmount } from './amounts'
import { failureText, useSetDeclaredFigure } from './queries'

type Field = 'tax_due' | 'balance' | 'advances'

const FIELDS: { name: Field; label: string; hint: string; signed: boolean }[] = [
  { name: 'tax_due', label: 'Imposta dovuta', hint: 'Mai negativa.', signed: false },
  { name: 'balance', label: 'Saldo', hint: 'Un credito si scrive con il segno meno.', signed: true },
  { name: 'advances', label: 'Acconti', hint: 'Mai negativi.', signed: false },
]

/**
 * «Digita il dichiarato»: the figures of the tax return as the accountant computed them, for one
 * tax and year, prefilled with the ones already typed. A field left empty is recorded as not
 * given (never as zero), at least one is needed, and saving replaces the three together; the
 * server has the last word and its sentence is shown as it is.
 */
export function DeclaredFigureDialog({
  legalEntityId,
  year,
  tax,
  current,
  onClose,
}: {
  legalEntityId: string
  year: number
  tax: Tax
  current: DeclaredFigure | undefined
  onClose: () => void
}) {
  const [values, setValues] = useState<Record<Field, string>>({
    tax_due: current?.tax_due ?? '',
    balance: current?.balance ?? '',
    advances: current?.advances ?? '',
  })
  const [errors, setErrors] = useState<Partial<Record<Field, string>>>({})
  const [problem, setProblem] = useState<string | null>(null)
  const save = useSetDeclaredFigure(legalEntityId)
  function submit() {
    setProblem(null)
    const found: Partial<Record<Field, string>> = {}
    const body: Record<Field, string | null> = { tax_due: null, balance: null, advances: null }
    for (const field of FIELDS) {
      const parsed = parseAmount(values[field.name], field.signed)
      if ('error' in parsed) found[field.name] = parsed.error
      else body[field.name] = parsed.value
    }
    setErrors(found)
    if (Object.keys(found).length > 0) return
    if (FIELDS.every((field) => body[field.name] === null)) {
      setProblem('Indica almeno una cifra: imposta dovuta, saldo o acconti.')
      return
    }
    save.mutate({ tax, year, ...body }, { onSuccess: onClose, onError: (error) => setProblem(failureText(error)) })
  }
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {current ? 'Correggi' : 'Digita'} il dichiarato: {TAX_LABELS[tax]} {year}
          </DialogTitle>
          <DialogDescription>
            Le cifre della dichiarazione come le ha calcolate il commercialista. Una cifra lasciata
            vuota resta non indicata, non vale zero. Salvare sostituisce le tre cifre insieme.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          {FIELDS.map((field) => (
            <div key={field.name} className="space-y-1">
              <Label htmlFor={`declared-${field.name}`}>{field.label}</Label>
              <Input
                id={`declared-${field.name}`}
                inputMode="decimal"
                value={values[field.name]}
                onChange={(event) => setValues({ ...values, [field.name]: event.target.value })}
                aria-invalid={errors[field.name] ? true : undefined}
                className="tabular-nums"
              />
              <p className={errors[field.name] ? 'text-sm text-destructive' : 'text-xs text-muted-foreground'}>
                {errors[field.name] ?? field.hint}
              </p>
            </div>
          ))}
        </div>
        {problem ? <p role="alert" className="text-sm text-destructive">{problem}</p> : null}
        {save.isPending ? <LoadingState label="Salvo il dichiarato…" /> : null}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Annulla
          </Button>
          <Button onClick={submit} disabled={save.isPending}>
            Salva il dichiarato
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
