import type { CardPersona, PersonaDifference, PersonaDifferenceField } from '@/lib/api'
import { WebLink } from '@/components/WebLink'
import { Row } from '../list-kit'

const DIFFERENCE_LABELS: Record<PersonaDifferenceField, string> = {
  nome: 'Nome',
  cognome: 'Cognome',
  email: 'Email',
  telefono: 'Telefono',
  linkedin_url: 'LinkedIn',
}

/** What the CV states, set beside the profile's own value (REB-855): the profile is never
 *  rewritten from the CV, so each CV value is marked as coming from it. */
export function CvMark() {
  return <> · dal CV</>
}

/** The alert at the top of the profile card where the CV and the profile both say
 *  something and differ: the admin decides which is right. */
export function CvDifferences({ differenze }: { differenze: PersonaDifference[] }) {
  if (differenze.length === 0) return null
  return (
    <div role="alert" className="space-y-1 rounded-md border border-destructive/40 p-3 text-sm">
      <p className="font-medium text-destructive">Il CV e il profilo non coincidono</p>
      <ul>
        {differenze.map((difference) => (
          <li key={difference.campo}>
            {DIFFERENCE_LABELS[difference.campo]}: nel CV «{difference.cv}», nel profilo «{difference.profilo}»
          </li>
        ))}
      </ul>
    </div>
  )
}

const digits = (phone: string) => phone.replace(/\D/g, '')

/** «Telefono»: the profile's, else the CV's marked «dal CV»; when both exist and differ,
 *  the CV's on a second line. */
export function CvPhoneRows({ telefono, persona }: { telefono: string | null; persona: CardPersona | null }) {
  const cv = persona?.telefono ?? null
  return (
    <Row label="Telefono">
      {telefono ? (
        <>
          {telefono}
          {cv && digits(cv) !== digits(telefono) && (
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
export function CvEmailRow({ email, persona }: { email: string; persona: CardPersona | null }) {
  const cvEmail = persona?.email && persona.email.toLowerCase() !== email.toLowerCase() ? persona.email : null
  if (!cvEmail) return null
  return (
    <Row label="Email nel CV">
      <a className="underline underline-offset-2" href={`mailto:${cvEmail}`}>
        {cvEmail}
      </a>
    </Row>
  )
}

export function CvCityRow({ persona }: { persona: CardPersona | null }) {
  if (!persona?.citta) return null
  return (
    <Row label="Città">
      {persona.citta}
      <CvMark />
    </Row>
  )
}

/** «Link»: the profile's, then the CV's others not already among them, marked «dal CV». */
export function CvLinksRow({ links, persona }: { links: string[]; persona: CardPersona | null }) {
  const fromCv = (persona?.altri_link ?? []).filter((link) => !links.includes(link))
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

export function CvCompaniesRow({ persona }: { persona: CardPersona | null }) {
  if (!persona?.aziende.length) return null
  return (
    <Row label="Aziende e clienti">
      {persona.aziende.join(', ')}
      <CvMark />
    </Row>
  )
}
