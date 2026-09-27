import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { peopleLabel, scheduleLabel } from '@/lib/campaigns'

export type Quando = 'adesso' | 'programma'

/** Invio (REB-526): the bar at the foot of the page, always in sight. One button that
 *  says what it will do, to how many and when; when it cannot, the sentence beside it
 *  says why. */
export function BarraInvio({
  mode,
  onMode,
  when,
  onWhen,
  riceveranno,
  blocked,
  pending,
  failure,
  onSend,
}: {
  mode: Quando
  onMode: (mode: Quando) => void
  when: { giorno: string; ora: string }
  onWhen: (when: { giorno: string; ora: string }) => void
  /** `null` until the list is in: the button then says no number rather than a zero. */
  riceveranno: number | null
  /** Why the button is off, or `null` when it is on. */
  blocked: string | null
  pending: boolean
  failure: string | null
  onSend: () => void
}) {
  const people = riceveranno === null ? null : peopleLabel(riceveranno)
  const moment = mode === 'programma' && when.giorno && when.ora ? scheduleLabel(when.giorno, when.ora) : null
  const target = people === null ? '' : mode === 'programma' ? ` per ${people}` : ` a ${people}`
  const label = `${mode === 'programma' ? 'Programma' : 'Invia'}${target}${moment ? `, ${moment}` : ''}`
  const hint =
    mode === 'programma'
      ? 'Ora di Roma. Fino alla partenza puoi riportarla in bozza.'
      : 'Parte entro un minuto. Fino ad allora puoi riportarla in bozza.'
  return (
    <section
      aria-label="Invio"
      className="sticky bottom-0 z-10 flex flex-wrap items-end justify-between gap-x-6 gap-y-3 border-t bg-card px-6 py-3"
    >
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex gap-2" role="group" aria-label="Quando parte">
          <Button type="button" variant={mode === 'adesso' ? 'default' : 'outline'} aria-pressed={mode === 'adesso'} onClick={() => onMode('adesso')}>
            Adesso
          </Button>
          <Button
            type="button"
            variant={mode === 'programma' ? 'default' : 'outline'}
            aria-pressed={mode === 'programma'}
            onClick={() => onMode('programma')}
          >
            Programma
          </Button>
        </div>
        {mode === 'programma' && (
          <>
            <div className="space-y-1">
              <Label htmlFor="campagna-giorno" className="text-xs">
                Giorno
              </Label>
              <Input id="campagna-giorno" type="date" value={when.giorno} onChange={(event) => onWhen({ ...when, giorno: event.target.value })} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="campagna-ora" className="text-xs">
                Ora
              </Label>
              <Input id="campagna-ora" type="time" value={when.ora} onChange={(event) => onWhen({ ...when, ora: event.target.value })} />
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
