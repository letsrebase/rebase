import { Button } from '@rebase/ui/button'
import type { StalePhase } from './staleRow'

/** The banner the three whole-row settings panels show for a 409 `stale_row` (see
 *  `staleRow.ts`), with the way out: «Ricarica», which keeps the draft. */
export function StaleRowBanner({
  phase,
  onReload,
  reloading,
}: {
  phase: Exclude<StalePhase, 'none'>
  onReload: () => void
  reloading: boolean
}) {
  return (
    <div
      role="alert"
      className="border-destructive/50 bg-destructive/10 text-destructive flex flex-wrap items-center justify-between gap-2 border px-3 py-2 text-sm"
    >
      {phase === 'refused' ? (
        <span>
          <strong>Qualcun altro ha salvato nel frattempo.</strong> Ricarica per vedere i suoi
          valori: quello che hai scritto resta nei campi che hai toccato.
        </span>
      ) : (
        <span>
          <strong>Riga ricaricata.</strong> I campi che non avevi toccato mostrano i valori
          salvati da qualcun altro, quelli che avevi modificato tengono i tuoi. Controlla e
          salva di nuovo.
        </span>
      )}
      {phase === 'refused' ? (
        <Button type="button" variant="outline" size="sm" onClick={onReload} disabled={reloading}>
          {reloading ? 'Ricarico…' : 'Ricarica'}
        </Button>
      ) : null}
    </div>
  )
}
