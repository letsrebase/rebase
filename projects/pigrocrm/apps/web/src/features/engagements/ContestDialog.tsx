import { useState } from 'react'
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
import { Label } from '@rebase/ui/label'
import { Loader } from '@rebase/ui/loader'
import { toast } from '@rebase/ui/sonner'
import { Textarea } from '@rebase/ui/textarea'
import { formatDate, formatQuantity, monthLabel, type ReportDay } from './labels'
import { monthStart } from './monthRows'
import { useApproveMonthlyReport } from './queries'
import { holdLabel } from './words'

const REASON_MAX = 1000


/**
 * «Contesta dei giorni…» (decision 3 B): the days of the month with a box each, one reason for all of
 * them, and one button that holds the ticked days and approves the rest. A person's act: the button is
 * the only way, no tool does it. A full screen on a phone, a dialog from `sm` up. The days' descriptions
 * are text other people wrote and are rendered as text, ellipsised, with the whole on hover.
 */
export function ContestDialog({
  engagementId,
  month,
  days,
  onClose,
}: {
  engagementId: string
  /** `2026-10`. */
  month: string
  /** The month's days as the report reads them. */
  days: readonly ReportDay[]
  onClose: () => void
}) {
  const approve = useApproveMonthlyReport(engagementId)
  const [held, setHeld] = useState<ReadonlySet<string>>(new Set())
  const [reason, setReason] = useState('')
  // Only a day that counts and is inside the letter's dates can be paid, so only that can be held.
  // A date can hold several entries (a work unit and a time entry): one box holds the date, which
  // is what the server holds, and every entry reads under it with its own quantity.
  const byDate = new Map<string, ReportDay[]>()
  for (const day of days) {
    if (!day.counts || !day.in_term) continue
    byDate.set(day.date, [...(byDate.get(day.date) ?? []), day])
  }
  const holdable = [...byDate.entries()]
  const count = held.size
  const toggle = (date: string, next: boolean) => {
    const copy = new Set(held)
    if (next) copy.add(date)
    else copy.delete(date)
    setHeld(copy)
  }
  return (
    <Dialog open onOpenChange={(next) => !next && !approve.isPending && onClose()}>
      <DialogContent
        className={cn(
          'max-h-dvh overflow-y-auto',
          'max-sm:inset-0 max-sm:h-dvh max-sm:max-w-none max-sm:translate-x-0 max-sm:translate-y-0 max-sm:rounded-none',
          'sm:max-w-xl',
        )}
      >
        <DialogHeader>
          <DialogTitle>Contesta dei giorni di {monthLabel(monthStart(month))}</DialogTitle>
          <DialogDescription>
            Spunta i giorni che il cliente contesta e scrivi il motivo. I giorni trattenuti non si
            pagano; il resto del mese viene approvato.
          </DialogDescription>
        </DialogHeader>
        {approve.isError ? <QueryErrorBanner error={approve.error} /> : null}
        {holdable.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nessun giorno da trattenere in questo mese.</p>
        ) : (
          <ul aria-label="Giorni del mese" className="max-h-[40dvh] divide-y overflow-y-auto border">
            {holdable.map(([date, entries]) => {
              const id = `contest-${date}`
              return (
                <li key={date} className="flex items-start gap-3 px-3 py-2">
                  <Checkbox
                    id={id}
                    checked={held.has(date)}
                    onCheckedChange={(next) => toggle(date, next === true)}
                    className="mt-1"
                  />
                  <Label htmlFor={id} className="min-w-0 flex-1 flex-col items-start gap-0.5 font-normal">
                    <span className="tabular-nums">{formatDate(date)}</span>
                    {entries.map((entry, index) => (
                      <span
                        key={`${entry.source}-${index}`}
                        className="block max-w-full truncate text-xs text-muted-foreground"
                        title={entry.description}
                      >
                        {formatQuantity(entry.quantity)} · {entry.description}
                      </span>
                    ))}
                  </Label>
                </li>
              )
            })}
          </ul>
        )}
        <div className="space-y-2">
          <Label htmlFor="contest-reason">Motivo</Label>
          <Textarea
            id="contest-reason"
            value={reason}
            rows={3}
            maxLength={REASON_MAX}
            placeholder="Perché il cliente contesta questi giorni."
            onChange={(event) => setReason(event.target.value)}
          />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={approve.isPending}>
            Annulla
          </Button>
          <Button
            disabled={approve.isPending || count === 0 || reason.trim() === ''}
            onClick={() =>
              approve.mutate(
                {
                  month,
                  body: { held_dates: [...held].sort(), objection: reason.trim() },
                },
                {
                  onSuccess: () => {
                    toast.success('Mese approvato, con i giorni trattenuti.')
                    onClose()
                  },
                },
              )
            }
          >
            {approve.isPending ? <Loader className="mr-2 size-4" /> : null}
            {holdLabel(count)}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
