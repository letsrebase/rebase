import { capture } from '@rebase/analytics/browser'
import { Link, useSearch } from '@tanstack/react-router'
import { ArrowUpRight, Download, Pencil, Plus } from 'lucide-react'
import type { ReactNode } from 'react'
import { Button } from '@rebase/ui/button'
import { member, type MemberRequest } from '@/lib/api'
import { formatBytes, formatDate, formatDay, formatEuro } from '@/lib/format'
import { toApplication, toCompanyApplication, useMe } from '@/lib/me'
import { GUIDE } from '@/lib/perks'
import { COMPANY_FIELDS } from '@/pages/CompanyWizard'
import { FREELANCER_FIELDS } from '@/pages/FreelancerWizard'
import { MemberContratti } from '@/pages/member/Contratti'
import { MemberReferral } from '@/pages/member/Referral'

// `/app/login`, not `/app/register`: PigroCRM's own login already knows what to do with
// whoever is behind it (REB-377's identity-cookie chooser, its own effect that sends an
// already-authenticated visitor straight to their dashboard, and REB-482's "Crea un
// nuovo spazio" beside the chooser too) and offers "Crea il tuo spazio" to anyone with
// nothing to open yet. `/app/register` used to sit here and always opened its own
// two-step form first, even to a member who already had a space -- its own hub-lookup
// only ran after that, one screen further in, and only then offered a link to enter
// instead of creating a second one.
const PIGROCRM_URL = 'https://pigro.letsrebase.com/app/login'
const ROLE_LABELS: Record<string, string> = { admin: 'Amministratore', member: 'Membro' }

/** One label and its answer, drawn as a row of a ledger: side by side from `sm`, the
 *  answer under its label on a phone, where a 160px label column would leave the answer
 *  a sliver. */
function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1 px-4 py-2.5 text-sm sm:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] sm:gap-4">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words font-medium">{children}</dd>
    </div>
  )
}

/** What the wizard's own field says about one answer of a request (`remoto`'s «Ibrido ·
 *  3 giorni in sede», `numero_risorse`'s «2 persone»), so the list and the edit form use
 *  the same words. */
function said(id: 'remoto' | 'numero_risorse', request: MemberRequest): string {
  const field = COMPANY_FIELDS.find((candidate) => candidate.id === id)
  return field?.summary(toCompanyApplication(request)) || '—'
}

function Cell({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="bg-card px-4 py-2.5 text-sm">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 break-words font-medium tabular-nums">{children}</dd>
    </div>
  )
}

/** One request, compact: the role and when it was filed, its own «Modifica», the project
 *  clipped to two lines, then the four answers that tell requests apart in a hairline
 *  grid. The whole text is on the edit page. */
function RequestCard({ request }: { request: MemberRequest }) {
  return (
    <li className="border bg-card">
      <div className="flex items-start justify-between gap-3 px-4 pt-3">
        <div className="min-w-0">
          <h3 className="break-words font-semibold">{request.figura_richiesta}</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">Inviata il {formatDate(request.created_at)}</p>
        </div>
        <Button asChild variant="outline" size="sm" className="shrink-0">
          <Link
            to="/me/edit-company/$id"
            params={{ id: request.id }}
            aria-label={`Modifica la richiesta: ${request.figura_richiesta}, del ${formatDate(request.created_at)}`}
          >
            <Pencil className="mr-2 size-4" />
            Modifica
          </Link>
        </Button>
      </div>
      {/* The padding sits on the wrapper: on the clamped element itself the lines past the
          second would show through its bottom padding. */}
      <div className="px-4 pt-2 pb-3">
        <p className="line-clamp-2 text-sm text-muted-foreground">{request.progetto}</p>
      </div>
      <dl className="grid grid-cols-2 gap-(--line) border-t bg-border">
        <Cell label="Budget">{formatEuro(request.budget_giornaliero)} / giorno</Cell>
        <Cell label="Da quando">
          {formatDay(request.periodo_da)}, {request.durata}
        </Cell>
        <Cell label="Modalità">{said('remoto', request)}</Cell>
        <Cell label="Persone">{said('numero_risorse', request)}</Cell>
      </dl>
    </li>
  )
}

/** What the person sent, under the wizard's own questions, and the perks. The email is
 *  shown and not editable: it is the address the link proved.
 *
 *  `useMe()` answers member and admin alike (REB-279): a `users` row is no longer
 *  guaranteed to carry a freelancer card, so the card section renders only when
 *  `ha_scheda` is true, and the company section only when `ha_azienda` is true
 *  (REB-314); a person with neither sees their name, email and role instead of a
 *  wizard-shaped section reading from fields that are all `null`. A person can carry
 *  both, and both render together. The two perks render only for a freelancer or an
 *  admin (`value` truthy or `profile.role === 'admin'`), never for a company-only
 *  referente: PigroCRM and the guide are pitched at a solo freelancer, and neither
 *  means anything to a company that came here to find people (REB-385, Lorenzo
 *  2026-09-23). A person who is both a freelancer and a company referente still
 *  qualifies through the freelancer card, so both perks keep showing.
 *
 *  A card also gets «Contratti» (REB-392), which reads its own route and renders
 *  nothing wizard-shaped.
 *
 *  A company sees every request it filed, newest first, each with its own «Modifica»
 *  (REB-602), and «Richiedi una nuova figura» as the section's action.
 *
 *  From `lg` the page is two columns of its own, each stacking independently so a tall
 *  neighbour leaves no gap: with a card, the card and «Contratti» on the left, the
 *  requests, the perks and the referral link on the right; without one, the requests
 *  (or the role, for a person with neither) take the left and the perks and the link
 *  the right, so no column is ever empty. Under `lg` the columns collapse in reading
 *  order: card, contracts, requests, perks, referral.
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
  const requests = profile.richieste
  const showPerks = value !== null || profile.role === 'admin'

  const cardSection = value && (
    <section aria-labelledby="me-scheda" className="space-y-3">
      <h2 id="me-scheda" className="text-lg font-semibold tracking-tight">
        La tua scheda
      </h2>
      {!profile.completa && (
        // What is missing is what a company would search by, so it is said here and not
        // only in the dashes below. Two ways to get here: a card an admin wrote from a
        // signup (ORB-155), and a person who skipped the CV in the wizard, where it is
        // optional. For the second the CV is the only thing that can be missing.
        <div role="status" className="border-(length:--line-strong) border-accent bg-card p-4 text-sm">
          <p>
            La tua scheda è incompleta. Aggiungi CV, tariffa, posizione e modalità di lavoro
            perché le aziende possano trovarti.
          </p>
          <Button asChild size="sm" className="mt-3">
            <Link to="/me/edit">Completa la scheda</Link>
          </Button>
        </div>
      )}
      <dl className="divide-y border bg-card">
        {fields.map((field) => (
          <Row key={field.id} label={field.label}>
            {field.summary(value) || '—'}
          </Row>
        ))}
        <Row label="Il tuo CV">
          {profile.cv_filename === null || profile.cv_size === null ? (
            <span className="font-normal text-muted-foreground">Nessun CV</span>
          ) : (
            <a href={member.cvUrl} className="inline-flex items-center gap-1.5 underline-offset-2 hover:underline">
              <Download className="size-4 shrink-0" aria-hidden="true" />
              {profile.cv_filename}
              <span className="font-normal text-muted-foreground">({formatBytes(profile.cv_size)})</span>
            </a>
          )}
        </Row>
      </dl>
    </section>
  )

  const requestsSection = requests.length > 0 && (
    <section aria-labelledby="me-richieste" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="me-richieste" className="text-lg font-semibold tracking-tight">
          {requests.length === 1 ? 'La tua richiesta' : 'Le tue richieste'}
        </h2>
        <Button asChild variant="outline" size="sm">
          <Link to="/me/new-company">
            <Plus className="mr-2 size-4" />
            Richiedi una nuova figura
          </Link>
        </Button>
      </div>
      <ul className="space-y-3">
        {requests.map((request) => (
          <RequestCard key={request.id} request={request} />
        ))}
      </ul>
    </section>
  )

  const roleSection = !value && requests.length === 0 && (
    <section aria-label="Chi sei">
      <dl className="divide-y border bg-card">
        <Row label="Ruolo">{ROLE_LABELS[profile.role] ?? profile.role}</Row>
      </dl>
    </section>
  )

  const perksSection = showPerks && (
    <section aria-label="I tuoi vantaggi" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-1">
      <div className="flex flex-col gap-3 border-(length:--line-strong) border-foreground bg-card p-5">
        <div className="flex-1 space-y-2">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Per chi è dentro</p>
          <h2 className="text-lg font-semibold">PigroCRM è tuo, gratis</h2>
          <p className="text-sm text-muted-foreground">
            Preventivo, contratto, fattura, ore: fatturare e farti pagare, con i dati fiscali già giusti.
          </p>
        </div>
        <Button asChild className="self-start">
          <a href={PIGROCRM_URL} target="_blank" rel="noopener noreferrer">
            Apri PigroCRM
            <ArrowUpRight className="ml-2 size-4" />
          </a>
        </Button>
        {/* Keeps the two perks' buttons on one line while they sit side by side. */}
        <p className="invisible hidden text-xs sm:block lg:hidden" aria-hidden="true">
          &nbsp;
        </p>
      </div>
      <div className="flex flex-col gap-3 border-(length:--line-strong) border-foreground bg-card p-5">
        <div className="flex-1 space-y-2">
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
  )

  return (
    <div className="mx-auto max-w-6xl space-y-8 p-6">
      {negato && (
        <p role="status" className="border-(length:--line-strong) border-accent bg-card p-4 text-sm">
          Quella sezione è riservata a chi amministra l’hub: eccoti nella tua area.
        </p>
      )}

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">La tua area</p>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight">
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

      <div className="grid items-start gap-8 lg:grid-cols-2 lg:gap-x-8">
        <div className="min-w-0 space-y-8">
          {value ? (
            <>
              {cardSection}
              <MemberContratti />
            </>
          ) : (
            (requestsSection || roleSection)
          )}
        </div>
        <div className="min-w-0 space-y-8">
          {value && requestsSection}
          {perksSection}
          <MemberReferral />
        </div>
      </div>
    </div>
  )
}
