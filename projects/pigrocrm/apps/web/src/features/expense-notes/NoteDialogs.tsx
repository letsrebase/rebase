import { useState, type ReactNode } from 'react'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import { cn } from '@rebase/ui/cn'
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
import { Textarea } from '@rebase/ui/textarea'
import { formatMoneyValue } from '@/features/time/columns'
import { monthOnly } from '@/features/staff/labels'
import { toIsoDate } from '@/lib/dates'
import { MONEY_SCALE, sumDecimalStrings } from '@/lib/decimal'
import { notes as notesWord } from './labels'
import { useReimburseNote, useRejectNote, type ExpenseNote, type ReimburseBody } from './queries'

/**
 * «Rifiuta…» (the brief, as `ProposalDetail` does it): the reason the person reads. A rejection is
 * final, so the dialog says so, and the reason is asked for, since it is what the staff member sees
 * on their own card.
 */
export function RejectDialog({
  note,
  onClose,
  onRejected,
}: {
  note: ExpenseNote
  onClose: () => void
  onRejected: () => void
}) {
  const [reason, setReason] = useState('')
  const reject = useRejectNote()
  const typed = reason.trim()
  return (
    <Dialog open onOpenChange={(next) => !next && !reject.isPending && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Rifiuta la nota</DialogTitle>
          <DialogDescription>
            Il motivo lo legge {note.staff_member_name}. Una nota rifiutata è definitiva: una nota
            corretta è una nota nuova.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="expense-note-reason">Motivo</Label>
          <Textarea
            id="expense-note-reason"
            value={reason}
            rows={3}
            placeholder="Per esempio: manca lo scontrino."
            onChange={(event) => setReason(event.target.value)}
          />
        </div>
        {reject.error === null ? null : <QueryErrorBanner error={reject.error} />}
        <DialogFooter>
          <Button variant="outline" disabled={reject.isPending} onClick={onClose}>
            Annulla
          </Button>
          <Button
            variant="destructive"
            disabled={typed === '' || reject.isPending}
            onClick={() =>
              reject.mutate(
                { id: note.id, reason: typed },
                {
                  onSuccess: () => {
                    toast.success('Nota rifiutata')
                    onRejected()
                  },
                },
              )
            }
          >
            {reject.isPending ? <Loader className="mr-2 size-4" /> : null}
            Rifiuta la nota
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

type Way = 'transfer' | 'payslip'

/** One option of the pair, a native radio under a label sized for a thumb: the keyboard and a
 *  screen reader behave as they do for any radio group, the label only draws it. */
function WayChoice({
  value,
  checked,
  onSelect,
  title,
  children,
}: {
  value: Way
  checked: boolean
  onSelect: (value: Way) => void
  title: string
  children?: ReactNode
}) {
  return (
    <div className={cn('space-y-3 border p-3', checked && 'border-foreground')}>
      <label className="flex min-h-9 cursor-pointer items-center gap-3 text-sm font-medium has-[:focus-visible]:ring-[3px] has-[:focus-visible]:ring-ring/50">
        <input
          type="radio"
          name="expense-note-way"
          value={value}
          checked={checked}
          onChange={() => onSelect(value)}
          className="size-4 accent-primary"
        />
        {title}
      </label>
      {checked ? children : null}
    </div>
  )
}

/**
 * «Rimborsa…» on a person's group of «Da rimborsare» (the brief, question 5): approving judged the
 * expense, and this is the one place that decides when the money leaves and in which payslip. One
 * bank transfer for the person's notes (a date, and whether it already left) or the payslip of a
 * month, which the consultant is told of through the «variazioni paghe» export and which adds no
 * second payment. The call is one per note, sent in order and stopped at the first refusal, which
 * is shown in the server's words with how many notes it did before.
 */
export function ReimburseDialog({
  personName,
  notes,
  onClose,
}: {
  personName: string
  notes: ExpenseNote[]
  onClose: () => void
}) {
  const [way, setWay] = useState<Way>('transfer')
  const [date, setDate] = useState(() => toIsoDate(new Date()))
  const [paid, setPaid] = useState(false)
  const [month, setMonth] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(0)
  const [problem, setProblem] = useState<unknown>(null)
  const reimburse = useReimburseNote()

  const body: ReimburseBody =
    way === 'transfer'
      ? { method: 'transfer', date, paid }
      : { method: 'payslip', paid: false, ...(month === '' ? {} : { payroll_month: `${month}-01` }) }
  const ready = way === 'payslip' || date !== ''

  async function submit() {
    setBusy(true)
    setProblem(null)
    setDone(0)
    let count = 0
    try {
      for (const note of notes) {
        await reimburse.mutateAsync({ id: note.id, body })
        count += 1
        setDone(count)
      }
    } catch (error) {
      setProblem(error)
      return
    } finally {
      setBusy(false)
    }
    toast.success(count === 1 ? 'Nota rimborsata' : `${notesWord(count)} rimborsate`)
    onClose()
  }

  return (
    <Dialog open onOpenChange={(next) => !next && !busy && onClose()}>
      <DialogContent className="gap-4 sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Rimborsa {personName}</DialogTitle>
          <DialogDescription>
            {notesWord(notes.length)}, {formatMoneyValue(sumDecimalStrings(notes.map((note) => note.amount), MONEY_SCALE))}.
            Scegli come glieli restituisci.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault()
            if (ready && !busy) void submit()
          }}
        >
          <WayChoice value="transfer" checked={way === 'transfer'} onSelect={setWay} title="Bonifico">
            <div className="space-y-3">
              <div className="space-y-1.5">
                <Label htmlFor="expense-note-transfer-date">Data del bonifico</Label>
                <Input
                  id="expense-note-transfer-date"
                  type="date"
                  value={date}
                  onChange={(event) => setDate(event.target.value)}
                />
              </div>
              <div className="flex items-center gap-2">
                <Checkbox
                  id="expense-note-paid"
                  checked={paid}
                  onCheckedChange={(next) => setPaid(next === true)}
                />
                <Label htmlFor="expense-note-paid">Già pagato</Label>
              </div>
            </div>
          </WayChoice>
          <WayChoice
            value="payslip"
            checked={way === 'payslip'}
            onSelect={setWay}
            title={month === '' ? 'In busta paga' : `In busta paga di ${monthOnly(month)}`}
          >
            <div className="space-y-1.5">
              <Label htmlFor="expense-note-payroll-month">Mese della busta paga</Label>
              <Input
                id="expense-note-payroll-month"
                type="month"
                value={month}
                onChange={(event) => setMonth(event.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                Lasciato vuoto è il prossimo mese senza cedolino. Lo dici al consulente con
                l'esportazione delle variazioni paghe e non nasce un secondo pagamento.
              </p>
            </div>
          </WayChoice>
          {problem === null ? null : (
            <div className="space-y-1">
              <QueryErrorBanner error={problem} />
              {done > 0 ? (
                <p role="status" className="text-sm text-muted-foreground">
                  Fatto per {done} su {notes.length}.
                </p>
              ) : null}
            </div>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" disabled={busy} onClick={onClose}>
              Annulla
            </Button>
            <Button type="submit" disabled={!ready || busy}>
              {busy ? <Loader className="mr-2 size-4" /> : null}
              Rimborsa
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
