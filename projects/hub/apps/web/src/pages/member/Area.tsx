import { capture } from '@rebase/analytics/browser'
import { Link } from '@tanstack/react-router'
import { ArrowUpRight, Download, LogOut, Pencil } from 'lucide-react'
import { Button } from '@rebase/ui/button'
import { member } from '@/lib/api'
import { formatBytes } from '@/lib/format'
import { GUIDE } from '@/lib/perks'
import { toApplication, useMember, useMemberLogout } from '@/lib/member'
import { FREELANCER_FIELDS } from '@/pages/FreelancerWizard'

const PIGROCRM_URL = 'https://pigro.letsrebase.com/app/registrati'

/** What the person sent, under the wizard's own questions, and the perks. The email is
 *  shown and not editable: it is the address the link proved.
 *
 *  The two perks are PigroCRM and the guide, and the guide is here rather than on the
 *  public site because it is a perk: `GET /api/hub/me/guida` answers a member and 401s
 *  everybody else (Lorenzo, ORB-70). */
export function Area() {
  const me = useMember()
  const logout = useMemberLogout()
  if (!me.data) return null
  const profile = me.data
  const value = toApplication(profile)
  const fields = FREELANCER_FIELDS.filter((field) => field.id !== 'email' && field.id !== 'cv')

  return (
    <div className="mx-auto max-w-2xl space-y-10">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">La tua area</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight">
            {profile.nome} {profile.cognome}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Ti scriviamo a <span className="font-medium text-foreground">{profile.email}</span>.
            Per cambiare indirizzo, rifai la candidatura con quello nuovo.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button asChild variant="outline" size="sm">
            <Link to="/io/modifica">
              <Pencil className="mr-2 size-4" />
              Modifica
            </Link>
          </Button>
          <Button variant="ghost" size="sm" onClick={() => logout.mutate()} disabled={logout.isPending}>
            <LogOut className="mr-2 size-4" />
            Esci
          </Button>
        </div>
      </header>

      {!profile.completa && (
        // What is missing is what a company would search by, so it is said here and not
        // only in the dashes below. Two ways to get here: a card an admin wrote from a
        // signup (ORB-155), and a person who skipped the CV in the wizard, where it is
        // optional. For the second the CV is the only thing that can be missing.
        <div role="status" className="rounded-2xl border-2 border-[var(--color-royal-gold)] bg-card p-5 text-sm">
          <p>
            La tua scheda è incompleta. Aggiungi CV, tariffa, posizione e modalità di lavoro
            perché le aziende possano trovarti.
          </p>
          <Button asChild size="sm" className="mt-3">
            <Link to="/io/modifica">Completa la scheda</Link>
          </Button>
        </div>
      )}

      <section aria-label="Quello che ci hai mandato">
        <dl className="divide-y rounded-2xl border bg-card">
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

      <section aria-label="I tuoi vantaggi" className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-3 rounded-2xl border-2 border-foreground bg-card p-6">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Per chi è dentro</p>
          <h2 className="text-lg font-semibold">PigroCRM è tuo, gratis</h2>
          <p className="text-sm text-muted-foreground">
            Preventivo, contratto, fattura, ore: fatturare e farti pagare, con i dati fiscali già giusti.
          </p>
          <Button asChild className="mt-auto self-start">
            <a href={PIGROCRM_URL}>
              Apri PigroCRM
              <ArrowUpRight className="ml-2 size-4" />
            </a>
          </Button>
        </div>
        <div className="flex flex-col gap-3 rounded-2xl border-2 border-foreground bg-card p-6">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Per chi è dentro</p>
          <h2 className="text-lg font-semibold">I primi passi da freelance</h2>
          <p className="text-sm text-muted-foreground">
            La parte che nessuno ti spiega prima della prima fattura: come dirti in una frase,
            come arrivare a un numero e difenderlo, cosa scrivere prima di iniziare. Venti minuti.
          </p>
          <Button asChild className="mt-auto self-start">
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
