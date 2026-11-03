import type { CardPerson, PersonDifference, PersonDifferenceField } from '@/lib/api'
import { WebLink } from '@/components/WebLink'
import { Row } from '../list-kit'

const DIFFERENCE_LABELS: Record<PersonDifferenceField, string> = {
  nome: 'Nome',
  last_name: 'Cognome',
  email: 'Email',
  phone: 'Telefono',
  linkedin_url: 'LinkedIn',
}

/** What the CV states, set beside the profile's own value (REB-855): the profile is never
 *  rewritten from the CV, so each CV value is marked as coming from it. */
export function CvMark() {
  return <> · dal CV</>
}

/** The alert at the top of the profile card where the CV and the profile both say
 *  something and differ: the admin decides which is right. */
export function CvDifferences({ differences }: { differences: PersonDifference[] }) {
  if (differences.length === 0) return null
  return (
    <div role="alert" className="space-y-1 rounded-md border border-destructive/40 p-3 text-sm">
      <p className="font-medium text-destructive">Il CV e il profilo non coincidono</p>
      <ul>
        {differences.map((difference) => (
          <li key={difference.field}>
            {DIFFERENCE_LABELS[difference.field]}: nel CV «{difference.cv}», nel profilo «{difference.profile}»
          </li>
        ))}
      </ul>
    </div>
  )
}

const digits = (phone: string) => phone.replace(/\D/g, '')

/** «Telefono»: the profile's, else the CV's marked «dal CV»; when both exist and differ,
 *  the CV's on a second line. */
export function CvPhoneRows({ phone, person }: { phone: string | null; person: CardPerson | null }) {
  const cv = person?.phone ?? null
  return (
    <Row label="Telefono">
      {phone ? (
        <>
          {phone}
          {cv && digits(cv) !== digits(phone) && (
            <span className="block">
              {cv}
              <CvMark />
            </span>
          )}
        </>
      ) : cv ? (
        `${cv} · dal CV`
      ) : (
        '—'
      )}
    </Row>
  )
}

/** «Email nel CV», only when it is not the account's. */
export function CvEmailRow({ email, person }: { email: string; person: CardPerson | null }) {
  const cvEmail = person?.email && person.email.toLowerCase() !== email.toLowerCase() ? person.email : null
  if (!cvEmail) return null
  return (
    <Row label="Email nel CV">
      <a className="underline underline-offset-2" href={`mailto:${cvEmail}`}>
        {cvEmail}
      </a>
    </Row>
  )
}

export function CvCityRow({ person }: { person: CardPerson | null }) {
  if (!person?.city) return null
  return (
    <Row label="Città">
      {person.city}
      <CvMark />
    </Row>
  )
}

/** «Link»: the profile's, then the CV's others not already among them, marked «dal CV». */
export function CvLinksRow({ links, person }: { links: string[]; person: CardPerson | null }) {
  const fromCv = (person?.other_links ?? []).filter((link) => !links.includes(link))
  return (
    <Row label="Link">
      {links.length || fromCv.length ? (
        <ul className="space-y-1">
          {links.map((link) => (
            <li key={link}>
              <WebLink href={link} />
            </li>
          ))}
          {fromCv.map((link) => (
            <li key={`cv-${link}`}>
              <WebLink href={link} />
              <CvMark />
            </li>
          ))}
        </ul>
      ) : (
        '—'
      )}
    </Row>
  )
}

export function CvCompaniesRow({ person }: { person: CardPerson | null }) {
  if (!person?.companies.length) return null
  return (
    <Row label="Aziende e clienti">
      {person.companies.join(', ')}
      <CvMark />
    </Row>
  )
}
