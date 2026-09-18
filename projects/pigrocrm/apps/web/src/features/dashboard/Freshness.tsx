import { RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button } from '@rebase/ui/button'

/**
 * §7.2: the age of the answer is shown, not implied. A number with no age is a number the
 * user believes is instantaneous, and this one can be up to sixty seconds old by design.
 *
 * The minute count re-renders on a 30-second interval, because "aggiornato 4 minuti fa"
 * that stays frozen at "adesso" is worse than no indicator: it makes a stale figure look
 * fresh.
 *
 * This is the one module under `features/dashboard/` that does arithmetic, and it does it
 * on a clock: `Date.now() - new Date(iso).getTime()` subtracts two instants and
 * `Math.floor(ms / 60_000)` divides one. `src/test/no-browser-arithmetic.test.ts` bans the
 * *coercion* of an API value into a JS number -- `Number()`, `parseFloat`, `parseInt`,
 * unary `+` -- which is a different thing and which nothing here does.
 */
function minutesSince(iso: string): number {
  const elapsedMs = Date.now() - new Date(iso).getTime()
  return Math.max(0, Math.floor(elapsedMs / 60_000))
}

export function Freshness({
  calcolatoAlle,
  onRefresh,
}: {
  calcolatoAlle: string
  onRefresh: () => void
}) {
  // The age is *derived* on every render, and the interval only forces one. Holding the
  // minute count in state instead would need a `setMinutes` in the effect to reset it when
  // `calcolatoAlle` changes -- which is a cascading render (`react-hooks/set-state-in-effect`
  // says so) and which also restarts the timer on every refetch, so the clock would drift
  // toward never ticking on a dashboard that refreshes. This way a new `calcolato_alle` is
  // correct in the same render that delivers it.
  const [, setTick] = useState(0)
  useEffect(() => {
    const timer = setInterval(() => setTick((previous) => previous + 1), 30_000)
    return () => clearInterval(timer)
  }, [])
  const minutes = minutesSince(calcolatoAlle)

  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground">
      <span>
        {minutes === 0
          ? 'Aggiornato adesso'
          : `Aggiornato ${minutes} ${minutes === 1 ? 'minuto' : 'minuti'} fa`}
      </span>
      <Button variant="ghost" size="icon-sm" onClick={onRefresh} aria-label="Ricalcola">
        <RefreshCw className="size-3.5" />
      </Button>
    </div>
  )
}
