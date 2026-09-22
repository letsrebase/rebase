import { Link } from '@tanstack/react-router'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Card, CardContent } from '@rebase/ui/card'
import { Skeleton } from '@rebase/ui/skeleton'
import { NOT_COMPUTABLE, formatPercent } from '@/features/analytics/format'
import { toProblem } from '@/lib/api'
import { money as euro } from './format'
import { useFiscalEstimate } from './queries'

/** The screen that owns the three parameters this estimate is derived from -- the
 *  profitability coefficient, the substitute-tax rate and the INPS rate all live on the
 *  fiscal profile, not on the hourly rates. */
const SETTINGS_LINK = (
  <Link to="/app/settings/fiscal" className="underline">
    imposta i parametri fiscali
  </Link>
)

function Row({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b py-2 last:border-0">
      <span className="text-muted-foreground">
        {label}
        {hint && <span className="ml-2 text-xs">{hint}</span>}
      </span>
      <span className="text-right font-medium tabular-nums">{value}</span>
    </div>
  )
}

/** A money line that the API could not compute, because one of its parameters is unset.
 *  «non calcolabile», never `0,00 €`: an estimated tax of zero reads as "you owe
 *  nothing", which is a claim nobody is in a position to make from a missing
 *  coefficient.
 *
 *  The figure itself goes through `./format`'s `money`, this folder's own formatter,
 *  which hands the API's decimal string to `Intl.NumberFormat` verbatim. Not
 *  `features/time`'s `formatMoneyValue`, which reaches the same output through
 *  `Number(value)`: identical on today's figures, and still the coercion criterion 14
 *  bans in a dashboard module -- borrowed from another folder rather than written here,
 *  which is how a rule stops applying without anybody deciding it should. */
function money(value: string | null): string {
  return value === null ? NOT_COMPUTABLE : euro(value)
}

/**
 * The annual fiscal estimate, with the word «stima» above the figures.
 *
 * Its position is the requirement, not its presence: a caveat at the bottom is read
 * after the decision has been made. The sentence itself is the backend's own
 * `avvertenza`, verbatim -- which caveats apply (the INPS floor and ceiling, other
 * income, payments on account, deductions) is a fiscal question, not a copywriting one,
 * and shortening it here would drop whichever one matters to this reader.
 *
 * The estimate is `admin`-only in the service, and the card is deliberately not hidden
 * from anybody: a non-admin gets the server's own refusal, which is an explanation,
 * rather than a feature that appears not to exist.
 *
 * `anno` is the caller's, not `new Date()`: this card lives in Home under the period
 * picker, and the estimate on screen has to be the one the period on screen names. It
 * kept its own clock while it was a screen of its own, which is the one thing that
 * changed when the Analisi section left the interface.
 *
 * `formatPercent` is the one import still taken from `features/analytics`, and it stays
 * there on purpose: `./format`'s `percent` is not the same formatter. It renders
 * «67,00%» where every screen of this product writes «67,00 %», and it answers a null
 * with a dash where this card has to say «non calcolabile» -- a rate nobody has set is
 * not a rate of nothing. So there is no dashboard-side equivalent to move to, and the
 * alternative is a fourth copy of a formatter this product has exactly one of. It is
 * also the one formatter left here whose implementation coerces (`Number()`, inside
 * `features/analytics/format.ts`, on a value the API has already divided and rounded);
 * `src/test/no-browser-arithmetic.test.ts` scans this folder's own source, so the
 * import is legal either way, and the money formatter above moved to `./format`
 * precisely because for money there *was* a local one to move to.
 */
export function FiscalPanel({ anno }: { anno: number }) {
  const estimate = useFiscalEstimate(anno)

  if (estimate.isError) {
    // `get_fiscal_estimate` raises `NotFound("fiscal_profile", "singleton")` when nothing
    // has been configured, whose detail is "fiscal_profile singleton not found" -- true,
    // English, and useless to the person who has to fix it. Every other failure keeps
    // the server's own words, including the 403.
    if (toProblem(estimate.error).status === 404) {
      return (
        <p className="text-muted-foreground">
          Profilo fiscale non configurato: senza il coefficiente di redditività e le due
          aliquote non c&apos;è niente da stimare. Vai in Impostazioni e {SETTINGS_LINK}.
        </p>
      )
    }
    return <QueryErrorBanner error={estimate.error} />
  }
  if (estimate.isLoading || !estimate.data) return <Skeleton className="h-64 w-full" />

  const data = estimate.data
  const missingParameters =
    data.coefficiente_redditivita === null ||
    data.aliquota_imposta_sostitutiva === null ||
    data.aliquota_inps === null

  return (
    <div className="space-y-4">
      {/* First, always. `role="note"` rather than `alert`: this is not a failure, it is
          the standing qualification on every figure below it. */}
      <p
        role="note"
        className="border bg-muted/50 px-3 py-2 text-sm text-muted-foreground"
      >
        {data.avvertenza}
      </p>

      <Card>
        <CardContent className="pt-6">
          <h2 className="mb-3 font-semibold">Stima fiscale {data.anno}</h2>
          <Row
            label="Ricavi incassabili"
            value={money(data.ricavi)}
            hint="fatture emesse nell'anno"
          />
          <Row
            label="Coefficiente di redditività"
            value={formatPercent(data.coefficiente_redditivita)}
          />
          <Row label="Imponibile" value={money(data.imponibile)} />
          <Row
            label="Aliquota imposta sostitutiva"
            value={formatPercent(data.aliquota_imposta_sostitutiva)}
          />
          <Row label="Imposta sostitutiva" value={money(data.imposta_sostitutiva)} />
          <Row label="Aliquota INPS" value={formatPercent(data.aliquota_inps)} />
          <Row label="Contributi INPS" value={money(data.contributi)} />
          <Row label="Reddito netto stimato" value={money(data.reddito_netto_stimato)} />
        </CardContent>
      </Card>

      {missingParameters && (
        <p className="text-sm text-muted-foreground">
          Alcune righe sono «{NOT_COMPUTABLE}» perché manca un parametro: {SETTINGS_LINK} in
          Impostazioni.
        </p>
      )}
    </div>
  )
}
