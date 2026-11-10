import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { toast } from '@rebase/ui/sonner'
import { toProblem } from '@/lib/api'
import { useSetPayDay, type PayDay } from './queries'

/** The day net pay leaves for everyone of the legal entity without a day of their own: a day
 *  of the month, in the month worked or the one after. The server holds it to 1–28. */
export function PayDayDialog({
  open,
  onOpenChange,
  legalEntityId,
  current,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  legalEntityId: string
  current: PayDay | undefined
}) {
  const [day, setDay] = useState(current?.pay_day === null || current === undefined ? '' : String(current.pay_day))
  const [offset, setOffset] = useState(String(current?.pay_month_offset ?? 0))
  const save = useSetPayDay(legalEntityId)
  const valid = /^\d{1,2}$/.test(day)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="gap-4 sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Giorno di paga</DialogTitle>
          <DialogDescription>
            Quando escono gli stipendi netti di chi non ha un giorno suo. Senza, le date non si possono stimare.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (!valid) return
            save.mutate(
              { pay_day: Number(day), pay_month_offset: Number(offset) },
              {
                onSuccess: () => {
                  toast.success('Giorno di paga salvato')
                  onOpenChange(false)
                },
              },
            )
          }}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="payday-day">Giorno del mese</Label>
              <Input id="payday-day" inputMode="numeric" value={day} onChange={(event) => setDay(event.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="payday-offset">Mese</Label>
              <Select value={offset} onValueChange={setOffset}>
                <SelectTrigger id="payday-offset" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="0">Quello lavorato</SelectItem>
                  <SelectItem value="1">Il mese dopo</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          {save.error === null ? null : (
            <p role="alert" className="border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {toProblem(save.error).detail}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Annulla
            </Button>
            <Button type="submit" disabled={!valid || save.isPending}>
              {save.isPending ? 'Salvo…' : 'Salva'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
