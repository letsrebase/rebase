import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { peopleLabel, scheduleLabel } from '@/lib/campaigns'

export type When = 'now' | 'schedule'

/** Invio (REB-526): the bar at the foot of the page, always in sight. One button that
 *  says what it will do, to how many and when; when it cannot, the sentence beside it
 *  says why. */
export function BarSend({
  mode,
  onMode,
  when,
  onWhen,
  will_receive,
  blocked,
  pending,
  failure,
  onSend,
}: {
  mode: When
  onMode: (mode: When) => void
  when: { day: string; time: string }
  onWhen: (when: { day: string; time: string }) => void
  /** `null` until the list is in: the button then says no number rather than a zero. */
  will_receive: number | null
  /** Why the button is off, or `null` when it is on. */
  blocked: string | null
  pending: boolean
  failure: string | null
  onSend: () => void
}) {
  const people = will_receive === null ? null : peopleLabel(will_receive)
  const moment = mode === 'schedule' && when.day && when.time ? scheduleLabel(when.day, when.time) : null
  const target = people === null ? '' : mode === 'schedule' ? ` per ${people}` : ` a ${people}`
  const label = `${mode === 'schedule' ? 'Programma' : 'Invia'}${target}${moment ? `, ${moment}` : ''}`
  const hint =
    mode === 'schedule'
      ? 'Ora di Roma. Fino alla partenza puoi riportarla in bozza.'
      : 'Parte entro un minuto. Fino ad allora puoi riportarla in bozza.'
  return (
    <section
      aria-label="Invio"
      className="sticky bottom-0 z-10 flex flex-wrap items-end justify-between gap-x-6 gap-y-3 border-t bg-card px-6 py-3"
    >
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex gap-2" role="group" aria-label="Quando parte">
          <Button type="button" variant={mode === 'now' ? 'default' : 'outline'} aria-pressed={mode === 'now'} onClick={() => onMode('now')}>
            Adesso
          </Button>
          <Button
            type="button"
            variant={mode === 'schedule' ? 'default' : 'outline'}
            aria-pressed={mode === 'schedule'}
            onClick={() => onMode('schedule')}
          >
            Programma
          </Button>
        </div>
        {mode === 'schedule' && (
          <>
            <div className="space-y-1">
              <Label htmlFor="campaign-day" className="text-xs">
                Giorno
              </Label>
              <Input id="campaign-day" type="date" value={when.day} onChange={(event) => onWhen({ ...when, day: event.target.value })} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="campaign-time" className="text-xs">
                Ora
              </Label>
              <Input id="campaign-time" type="time" value={when.time} onChange={(event) => onWhen({ ...when, time: event.target.value })} />
            </div>
          </>
        )}
      </div>
      <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-2">
        <p className="max-w-md text-xs text-muted-foreground sm:text-sm" aria-live="polite">
          {failure ? (
            <span role="alert" className="text-destructive">
              {failure}
            </span>
          ) : (
            (blocked ?? hint)
          )}
        </p>
        <Button type="button" size="lg" onClick={onSend} disabled={blocked !== null || pending}>
          {pending ? 'Un momento…' : label}
        </Button>
      </div>
    </section>
  )
}
