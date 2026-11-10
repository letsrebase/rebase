import { useState, type CSSProperties, type ReactNode } from 'react'
import { PHONE_BAR_HEIGHT } from '@/components/PhoneBar'
import { formatDay } from '@/features/export/labels'
import { useUsers } from '@/features/settings/queries'
import { toProblem } from '@/lib/api'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { SCOPED_CANNOT_CLOSE_SENTENCE } from './labels'
import type { LockMode } from './lock'
import { useCloseIvaPeriod, useReopenIvaPeriod, type IvaPeriod } from './queries'

/** «Periodo chiuso il 16/11/2026 da Giulia Ferri»: who closed it and when. The name is the member's
 *  current one, read from the members list; one it cannot find is left out, never guessed. */
export function ClosedBand({ period }: { period: IvaPeriod }) {
  const users = useUsers({ enabled: period.closed })
  if (!period.closed || period.closed_at === null) return null
  const name = users.data?.find((user) => user.id === period.closed_by)?.name
  return (
    <p role="status" className="border bg-card px-4 py-3 text-sm">
      Periodo chiuso il {formatDay(period.closed_at)}
      {name === undefined ? '' : ` da ${name}`}. Nessuna registrazione di questo periodo si modifica
      finché non viene riaperto.
    </p>
  )
}

export function ScopedNote() {
  return (
    <p role="note" className="border bg-card px-4 py-3 text-sm text-muted-foreground">
      {SCOPED_CANNOT_CLOSE_SENTENCE}
    </p>
  )
}

function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirm,
  pending,
  problem,
  onConfirm,
  destructive,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  confirm: string
  pending: boolean
  problem: string | null
  onConfirm: () => void
  destructive: boolean
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {problem !== null && (
          <p
            role="alert"
            className="border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
          >
            {problem}
          </p>
        )}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Annulla
          </Button>
          <Button
            type="button"
            variant={destructive ? 'destructive' : 'default'}
            disabled={pending}
            onClick={onConfirm}
          >
            {confirm}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/**
 * «Chiudi il periodo» or «Riapri», each behind its question. A refusal stays in the dialog in the
 * server's own words, since it names what is wrong (a payment froze the settlement, the period has
 * not ended); a success closes the dialog and the periods are read again.
 */
export function PeriodLockButton({
  period,
  mode,
  entityId,
  entityName,
  block = false,
}: {
  period: IvaPeriod
  mode: Extract<LockMode, 'close' | 'reopen'>
  entityId: string
  entityName: string
  /** Full width: the phone's sticky bar. */
  block?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const close = useCloseIvaPeriod()
  const reopen = useReopenIvaPeriod()
  const mutation = mode === 'close' ? close : reopen
  const target = { year: period.year, number: period.number, legalEntityId: entityId }
  const change = (next: boolean) => {
    setOpen(next)
    if (!next) setProblem(null)
  }
  const confirm = () => {
    setProblem(null)
    mutation.mutate(target, {
      onSuccess: () => setOpen(false),
      onError: (error) => setProblem(toProblem(error).detail),
    })
  }
  return (
    <>
      <Button
        type="button"
        variant={mode === 'close' ? 'destructive' : 'outline'}
        className={block ? 'w-full' : undefined}
        onClick={() => setOpen(true)}
      >
        {mode === 'close' ? 'Chiudi il periodo' : 'Riapri'}
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={change}
        title={mode === 'close' ? 'Chiudere il periodo?' : 'Riaprire il periodo?'}
        description={
          mode === 'close'
            ? `Chiudere il ${period.label} per ${entityName}? Dopo, nessuna registrazione di questo periodo si modifica. Un amministratore può riaprirlo.`
            : `Riaprire il ${period.label} per ${entityName}? Le registrazioni di questo periodo tornano modificabili e la riapertura viene registrata.`
        }
        confirm={mode === 'close' ? 'Chiudi il periodo' : 'Riapri il periodo'}
        pending={mutation.isPending}
        problem={problem}
        onConfirm={confirm}
        destructive={mode === 'close'}
      />
    </>
  )
}

/** The phone's bar for the lock button, sticky above the bottom bar so it is never covered. */
export function PhoneLockBar({ children }: { children: ReactNode }) {
  return (
    <div
      style={
        { '--phone-bar': `calc(${PHONE_BAR_HEIGHT} + env(safe-area-inset-bottom))` } as CSSProperties
      }
      className="sticky bottom-(--phone-bar) z-10 border-t bg-card px-8 py-3"
    >
      {children}
    </div>
  )
}
