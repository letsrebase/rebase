import { ContactPointsList } from '@/components/ContactPointsList'
import { WebLink } from '@/components/WebLink'
import type { FreelancerCard, Freelancer } from '@/lib/api'
import { distinctExtraPoints } from '@/lib/contactPoints'
import { REMOTE_LABELS, VETTED_LABEL, formatDate, formatDateTime } from '@/lib/format'
import { formatMoney } from '@rebase/ui/format'
import { Row } from '../list-kit'
import {
  CvCityRow,
  CvCompaniesRow,
  CvDifferences,
  CvEmailRow,
  CvLinksRow,
  CvMark,
  CvPhoneRows,
} from './CvRows'
import { FreelancerSignup } from './Subscription'

/** Who the answers on a card come from, as the «Scheda» row says it. */
function ownership(f: Freelancer): string {
  if (f.filled_by === 'person') return 'compilata dalla persona'
  return f.complete ? 'scritta dall’admin' : 'scritta dall’admin, da completare'
}

/**
 * «Contatti e dati»: every way to reach the person and what the hub knows of them, row by row
 * (REB-1158), with what the CV says marked «dal CV» beside the profile's own value, the CV's
 * differences on top, the newsletter sign-up and the PigroCRM space. «Altri recapiti» lists
 * only the points that are not already in a row of their own, so the primary phone is not said twice.
 */
export function ContactsTab({ f, cvCard }: { f: Freelancer; cvCard: FreelancerCard | undefined }) {
  const person = cvCard?.person ?? null
  const others = distinctExtraPoints(f.contact_points, [f.phone ?? null, person?.phone ?? null, f.email, person?.email ?? null])
  return (
    <div className="space-y-6">
      {person && <CvDifferences differences={cvCard?.differences ?? []} />}
      <dl className="space-y-3 text-sm">
        <Row label="Email">
          <a className="underline underline-offset-2" href={`mailto:${f.email}`}>
            {f.email}
          </a>
        </Row>
        <CvPhoneRows phone={f.phone ?? null} person={person} />
        {others.length > 0 && (
          <Row label="Altri recapiti">
            <ContactPointsList points={others} />
          </Row>
        )}
        <CvEmailRow email={f.email} person={person} />
        <CvCityRow person={person} />
        <Row label="Posizione">{f.job_title ?? '—'}</Row>
        <Row label="Tariffa a giornata">{f.daily_rate === null ? '—' : formatMoney(f.daily_rate)}</Row>
        <Row label="Modalità">{f.remote ? REMOTE_LABELS[f.remote] : '—'}</Row>
        <Row label="LinkedIn">
          {f.linkedin_url ? (
            <WebLink href={f.linkedin_url} />
          ) : person?.linkedin_url ? (
            <>
              <WebLink href={person.linkedin_url} />
              <CvMark />
            </>
          ) : (
            '—'
          )}
        </Row>
        <CvLinksRow links={f.links} person={person} />
        <CvCompaniesRow person={person} />
        <Row label="Arrivato">
          {formatDate(f.created_at)}
          {f.utm_source ? ` · da ${f.utm_source}` : ''}
          {f.origin ? ` · pagina ${f.origin}` : ''}
        </Row>
        <Row label="Provenienza">{f.provenance}</Row>
        <Row label="Accessi">
          {f.logins === 0 || f.last_access === null ? 'Mai entrato' : `${f.logins} · ultimo ${formatDateTime(f.last_access)}`}
        </Row>
        <Row label="Scheda">{ownership(f)}</Row>
        <Row label="Verifica">{f.vetted_at ? `${VETTED_LABEL} il ${formatDate(f.vetted_at)}` : 'Non verificato'}</Row>
      </dl>
      {!person && f.cv_filename !== null && f.deleted_at === null && cvCard && (
        <p className="text-sm text-muted-foreground">Dati dal CV non ancora letti: «Rigenera scheda» li legge.</p>
      )}
      <FreelancerSignup utm={f.signup_utm} />
      {f.pigro_slug && (
        <section className="space-y-2">
          <h2 className="text-sm font-medium">Spazio PigroCRM</h2>
          <p className="text-sm">
            <code className="border bg-muted px-1.5 py-0.5">{f.pigro_slug}</code>
          </p>
        </section>
      )}
    </div>
  )
}
