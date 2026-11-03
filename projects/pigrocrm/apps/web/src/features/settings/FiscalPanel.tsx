import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { useFiscalProfile } from '@/features/invoices/queries'
import { FiscalForm } from './FiscalForm'

export function FiscalPanel({ aziendaId }: { aziendaId: string }) {
  const profile = useFiscalProfile(aziendaId)
  // `data` is `undefined` only before the first successful read: a 404 is `null`.
  const unreadable = profile.isError && profile.data === undefined

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-medium">Fiscale</h2>
        <p className="text-muted-foreground text-sm">
          I parametri che decidono aliquota, natura e bollo su ogni riga di fattura. Una
          fattura emessa conserva una copia di questi valori, quindi cambiarli qui non
          tocca i documenti già emessi. Gli ultimi tre non finiscono in fattura: servono
          a stimare imposta sostitutiva e contributi sui compensi dell’anno.
        </p>
      </div>

      {profile.isError ? <QueryErrorBanner error={profile.error} /> : null}

      {!profile.isLoading && !unreadable && profile.data === null ? (
        <p className="text-muted-foreground text-sm">
          {profile.isError
            ? // A `null` from an earlier read beside a read that failed: what was true
              // then is not known to be true now, and the sentence says so instead of
              // presenting the absence as a fact (REB-622).
              'All’ultima lettura il profilo non era configurato; non è stato possibile verificarne lo stato attuale. La bozza resta nei campi.'
            : 'Profilo non ancora configurato: senza di esso non è possibile emettere fatture.'}
        </p>
      ) : null}

      {profile.isLoading || unreadable ? null : (
        // A read that never succeeded hides the form entirely (`unreadable`). A 404 is
        // not an error here (`useFiscalProfile` maps it to `null`, "not configured yet"),
        // so that only fires on a *real* failure: the row may well exist and simply be
        // unreadable, and rendering the blank form in that state invites somebody to
        // fill it in and press Salva, a PUT of every key over a profile the panel was
        // never able to show them. A refetch that fails *after* a read succeeded leaves
        // the form up, with the error above it: a draft and a stale refusal survive a
        // «Ricarica» that did not answer (REB-622).
        // Keyed on the azienda alone, so the form seeds at mount rather than in an
        // effect and a profile that appears under it (another admin's first save) is a
        // row that arrived, which the form follows while untouched and adopts on
        // «Ricarica», never a remount that drops the draft. Two aziende without a
        // profile still get a form each.
        <FiscalForm key={aziendaId} aziendaId={aziendaId} profile={profile.data ?? null} />
      )}
    </div>
  )
}
