import { Link } from '@tanstack/react-router'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Skeleton } from '@rebase/ui/skeleton'
import { AttivitaItem } from '@/features/attivita/AttivitaItem'
import { useAttivitaOggi } from '@/features/attivita/queries'
import { useAuth } from '@/lib/auth'

/** How many rows the band draws before it says «e altre N». */
export const BAND_ROWS = 5

/**
 * The Home's «Da fare» band (REB-874): the attività that are late and the ones due today,
 * above the analysis tabs, so the first screen of the product says what asks for a person
 * before it says how the books are doing.
 *
 * The cut is «mine and nobody's», the one the sidebar's overdue count and the Attività page
 * open on since REB-871: `assegnata_a` is the signed-in person plus `anche_non_assegnate`,
 * which is how an attività an agent created without naming anybody still shows up. The
 * counts in the heading are the server's real totals; the rows are the first
 * `BAND_ROWS` of «in ritardo» then «oggi», each an `AttivitaItem`, so «Fatto», «Modifica»
 * and the link to the record work here exactly as on the Attività page. Every attività
 * write invalidates the `queryKeys.activitiesAll` prefix this reads under, so «Fatto» takes the row
 * out, and the sidebar count with it, in one refetch.
 *
 * With nothing to do it is one line and not an empty card: a panel that says «0» in a box
 * is noise on the screen that opens the product.
 */
export function DaFareBand() {
  const { user } = useAuth()
  const panel = useAttivitaOggi(
    { assegnata_a: user?.id, anche_non_assegnate: true, limit: BAND_ROWS },
    { enabled: Boolean(user?.id) },
  )

  if (panel.isError) {
    return (
      <div className="px-8 pb-6">
        <QueryErrorBanner error={panel.error} />
      </div>
    )
  }
  if (!panel.data) {
    return (
      <div className="px-8 pb-6">
        <Skeleton className="h-16 w-full" />
      </div>
    )
  }

  const { in_ritardo: late, scadono_oggi: today } = panel.data
  const total = late.totale + today.totale
  if (total === 0) {
    return (
      <p className="px-8 pb-6 text-sm text-muted-foreground">
        Niente in ritardo né in scadenza oggi.
      </p>
    )
  }

  const rows = [
    ...late.items.map((attivita) => ({ attivita, isLate: true })),
    ...today.items.map((attivita) => ({ attivita, isLate: false })),
  ].slice(0, BAND_ROWS)
  const rest = total - rows.length

  return (
    <section aria-labelledby="home-da-fare" className="px-8 pb-6">
      <div className="space-y-3 border bg-background p-4">
        <h2 id="home-da-fare" className="text-base font-semibold">
          Da fare:{' '}
          {late.totale > 0 && <span className="text-destructive">{late.totale} in ritardo</span>}
          {late.totale > 0 && today.totale > 0 && ', '}
          {today.totale > 0 && <span>{today.totale} oggi</span>}
        </h2>
        <ul className="space-y-3 text-sm">
          {rows.map(({ attivita, isLate }) => (
            // The late ones say their day, which is what makes them late; today's do not
            // repeat it.
            <AttivitaItem key={attivita.id} attivita={attivita} showScadenza={isLate} />
          ))}
        </ul>
        <div className="flex items-center justify-between gap-3 text-sm">
          <span className="text-muted-foreground">{rest > 0 ? `e altre ${rest}` : null}</span>
          <Link
            to="/app/activities"
            search={{ vista: 'tutte' }}
            className="underline underline-offset-4"
          >
            Tutte le attività
          </Link>
        </div>
      </div>
    </section>
  )
}
