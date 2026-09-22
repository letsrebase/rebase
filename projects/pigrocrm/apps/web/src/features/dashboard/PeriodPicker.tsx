import { Button } from '@rebase/ui/button'
import { currentMonth, presetQuarter, presetYear, type Periodo } from './periodo'

/**
 * Two dates and three presets. Every change is handed straight back to the caller, which
 * puts it in the URL — nothing here is component state, because a period held locally is a
 * period a shared link cannot carry (§4).
 *
 * The presets are `outline` and not `ghost`: they sit where §4 puts a page's one strong
 * action, and with no line and no fill they read as a caption rather than as three things
 * you can press (visual pass of 2026-09-08). The dates are square inputs carrying the
 * same 1px ink line as every other control on a filter row.
 */
export function PeriodPicker({
  periodo,
  onChange,
}: {
  periodo: Periodo
  onChange: (next: Periodo) => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <label className="text-sm text-muted-foreground" htmlFor="periodo-da">
        Dal
      </label>
      <input
        id="periodo-da"
        type="date"
        value={periodo.da}
        onChange={(event) => onChange({ ...periodo, da: event.target.value })}
        className="min-w-0 border bg-background px-2 py-1 text-sm"
      />
      <label className="text-sm text-muted-foreground" htmlFor="periodo-a">
        al
      </label>
      <input
        id="periodo-a"
        type="date"
        value={periodo.a}
        onChange={(event) => onChange({ ...periodo, a: event.target.value })}
        className="min-w-0 border bg-background px-2 py-1 text-sm"
      />
      <Button variant="outline" size="sm" onClick={() => onChange(currentMonth())}>
        Mese
      </Button>
      <Button variant="outline" size="sm" onClick={() => onChange(presetQuarter())}>
        Trimestre
      </Button>
      <Button variant="outline" size="sm" onClick={() => onChange(presetYear())}>
        Anno
      </Button>
    </div>
  )
}
