import { capture } from '@rebase/analytics/browser'
import { Link, useSearch } from '@tanstack/react-router'
import { ArrowUpRight, Download, Pencil, Plus } from 'lucide-react'
import { Button } from '@rebase/ui/button'
import { member } from '@/lib/api'
import { formatBytes } from '@/lib/format'
import { toApplication, toCompanyApplication, useMe } from '@/lib/me'
import { GUIDE } from '@/lib/perks'
import { COMPANY_FIELDS } from '@/pages/CompanyWizard'
import { FREELANCER_FIELDS } from '@/pages/FreelancerWizard'

const PIGROCRM_URL = 'https://pigro.letsrebase.com/app/register'
const ROLE_LABELS: Record<string, string> = { admin: 'Amministratore', member: 'Membro' }

/** What the person sent, under the wizard's own questions, and the perks. The email is
 *  shown and not editable: it is the address the link proved.
 *
 *  `useMe()` answers member and admin alike (REB-279): a `users` row is no longer
 *  guaranteed to carry a freelancer card, so the card section renders only when
 *  `ha_scheda` is true, and the company section only when `ha_azienda` is true
 *  (REB-314); a person with neither sees their name, email and role instead of a
 *  wizard-shaped section reading from fields that are all `null`. A person can carry
 *  both, and both render together. The two perks stay unconditional -- PigroCRM and
 *  the guide are for the community, not for having applied through a wizard
 *  specifically.
 *
 *  The `negato` flag is set by `AdminGuard` when a signed-in non-admin is bounced off
 *  `/admin/*`: this is where they land, with a sentence saying why instead of a blank
 *  screen or a raw 403 (REB-279's own access rule). Logout lives in the signed-in
 *  shell's sidebar now, not here, so there is one button for it instead of two. */
export function Area() {
  const me = useMe()
  const { negato } = useSearch({ strict: false }) as { negato?: boolean }
  if (!me.data) return null
  const profile = me.data
  const value = profile.ha_scheda ? toApplication(profile) : null
  const fields = FREELANCER_FIELDS.filter((field) => field.id !== 'email' && field.id !== 'cv')
  const companyValue = profile.ha_azienda ? toCompanyApplication(profile) : null
  // Same six fields `ModificaAzienda.tsx`'s `editCompanyFields()` reaches (REB-314;
  // REB-380 adds the last three): `nome_azienda`, the referente and `telefono` stay
  // off the read-only view too, the identity this section never shows.
  const companyFields = COMPANY_FIELDS.filter(
    (field) =>
      field.id === 'progetto' ||
      field.id === 'periodo_da' ||
      field.id === 'budget_giornaliero' ||
      field.id === 'remoto' ||
      field.id === 'numero_risorse' ||
      field.id === 'figura_richiesta',
  )

  return (
    <div className="mx-auto max-w-2xl space-y-10 p-6">
      {negato && (
        <p role="status" className="border-(length:--line-strong) border-accent bg-card p-4 text-sm">
          Quella sezione è riservata a chi amministra l’hub: eccoti nella tua area.
        </p>
      )}

      <header className="flex flex-wrap items-start justify-between gap-4 pt-2">
        <div>
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">La tua area</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight">
            {profile.nome} {profile.cognome}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Ti scriviamo a <span className="font-medium text-foreground">{profile.email}</span>.
            {profile.ha_scheda && ' Per cambiare indirizzo, rifai la candidatura con quello nuovo.'}
          </p>
        </div>
        {profile.ha_scheda && (
          <Button asChild variant="outline" size="sm">
            <Link to="/me/edit">
              <Pencil className="mr-2 size-4" />
              Modifica
            </Link>
          </Button>
        )}
      </header>

      {value && (
        <>
          {!profile.completa && (
            // What is missing is what a company would search by, so it is said here and not
            // only in the dashes below. Two ways to get here: a card an admin wrote from a
            // signup (ORB-155), and a person who skipped the CV in the wizard, where it is
            // optional. For the second the CV is the only thing that can be missing.
            <div role="status" className="border-(length:--line-strong) border-accent bg-card p-5 text-sm">
              <p>
                La tua scheda è incompleta. Aggiungi CV, tariffa, posizione e modalità di lavoro
                perché le aziende possano trovarti.
              </p>
              <Button asChild size="sm" className="mt-3">
                <Link to="/me/edit">Completa la scheda</Link>
              </Button>
            </div>
          )}

          <section aria-label="Quello che ci hai mandato">
            <dl className="divide-y border bg-card">
              {fields.map((field) => (
                <div key={field.id} className="flex items-start gap-4 px-4 py-3 text-sm">
                  <dt className="w-40 shrink-0 text-muted-foreground">{field.label}</dt>
                  <dd className="min-w-0 flex-1 break-words font-medium">{field.summary(value) || '—'}</dd>
                </div>
              ))}
              <div className="flex items-start gap-4 px-4 py-3 text-sm">
                <dt className="w-40 shrink-0 text-muted-foreground">Il tuo CV</dt>
                <dd className="min-w-0 flex-1">
                  {profile.cv_filename === null || profile.cv_size === null ? (
                    <span className="text-muted-foreground">Nessun CV</span>
                  ) : (
                    <a href={member.cvUrl} className="inline-flex items-center gap-1.5 font-medium underline-offset-2 hover:underline">
                      <Download className="size-4" aria-hidden="true" />
                      {profile.cv_filename}
                      <span className="font-normal text-muted-foreground">({formatBytes(profile.cv_size)})</span>
                    </a>
                  )}
                </dd>
              </div>
            </dl>
          </section>
        </>
      )}

      {companyValue && (
        <section aria-label="La tua richiesta" className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <h2 className="text-lg font-semibold tracking-tight">La tua richiesta più recente</h2>
            <div className="flex items-center gap-2">
              <Button asChild variant="outline" size="sm">
                <Link to="/me/new-company">
                  <Plus className="mr-2 size-4" />
                  Richiedi una nuova figura
                </Link>
              </Button>
              <Button asChild variant="outline" size="sm">
                <Link to="/me/edit-company">
                  <Pencil className="mr-2 size-4" />
                  Modifica richiesta
                </Link>
              </Button>
            </div>
          </div>
          <dl className="divide-y border bg-card">
            {companyFields.map((field) => (
              <div key={field.id} className="flex items-start gap-4 px-4 py-3 text-sm">
                <dt className="w-40 shrink-0 text-muted-foreground">{field.label}</dt>
                <dd className="min-w-0 flex-1 break-words font-medium">
                  {field.summary(companyValue) || '—'}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      )}

      {!value && !companyValue && (
        <section aria-label="Chi sei">
          <dl className="divide-y border bg-card">
            <div className="flex items-start gap-4 px-4 py-3 text-sm">
              <dt className="w-40 shrink-0 text-muted-foreground">Ruolo</dt>
              <dd className="min-w-0 flex-1 font-medium">{ROLE_LABELS[profile.role] ?? profile.role}</dd>
            </div>
          </dl>
        </section>
      )}

      <section aria-label="I tuoi vantaggi" className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-3 border-(length:--line-strong) border-foreground bg-card p-6">
          <div className="flex-1 space-y-3">
            <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Per chi è dentro</p>
            <h2 className="text-lg font-semibold">PigroCRM è tuo, gratis</h2>
            <p className="text-sm text-muted-foreground">
              Preventivo, contratto, fattura, ore: fatturare e farti pagare, con i dati fiscali già giusti.
            </p>
          </div>
          <Button asChild className="self-start">
            <a href={PIGROCRM_URL}>
              Apri PigroCRM
              <ArrowUpRight className="ml-2 size-4" />
            </a>
          </Button>
          <p className="invisible text-xs text-muted-foreground" aria-hidden="true">
            &nbsp;
          </p>
        </div>
        <div className="flex flex-col gap-3 border-(length:--line-strong) border-foreground bg-card p-6">
          <div className="flex-1 space-y-3">
            <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Per chi è dentro</p>
            <h2 className="text-lg font-semibold">I primi passi da freelance</h2>
            <p className="text-sm text-muted-foreground">
              La parte che nessuno ti spiega prima della prima fattura: come dirti in una frase,
              come arrivare a un numero e difenderlo, cosa scrivere prima di iniziare. Venti minuti.
            </p>
          </div>
          <Button asChild className="self-start">
            <a href={member.guideUrl} onClick={() => capture('guida_scaricata')}>
              <Download className="mr-2 size-4" />
              Scarica la guida
            </a>
          </Button>
          <p className="text-xs text-muted-foreground">
            PDF, {GUIDE.pages} pagine, {GUIDE.kilobytes} KB.
          </p>
        </div>
      </section>
    </div>
  )
}
