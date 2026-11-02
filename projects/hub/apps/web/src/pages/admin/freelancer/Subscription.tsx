import type { Freelancer } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import { Row } from '../list-kit'

/** The sign-up's own attribution, if this address left one on the landing -- separate
 *  from the card's own `utm_source`, since the two can differ. */
type SignupAttribution = NonNullable<Freelancer['iscrizione_utm']>

const ISCRIZIONE_UTM_LABELS: [key: keyof SignupAttribution, label: string][] = [
  ['origine', 'Origine'],
  ['utm_source', 'Sorgente'],
  ['utm_medium', 'Medium'],
  ['utm_campaign', 'Campagna'],
  ['utm_content', 'Contenuto'],
  ['utm_term', 'Termine'],
  ['utm_id', 'Id'],
]

/** The sign-up this address left on the landing, if it did, with its own UTM
 *  (REB-284): a section of its own since an admin-drafted card copies the signup's
 *  UTM onto the card at creation but a wizard card keeps its own, and the two can
 *  genuinely differ from what «Arrivato» shows above. */
export function FreelancerIscrizione({ utm }: { utm: SignupAttribution | null | undefined }) {
  const known = utm ? ISCRIZIONE_UTM_LABELS.filter(([key]) => utm[key] != null) : []
  return (
    <section className="space-y-3 px-6 pb-6">
      <h2 className="text-sm font-medium">Iscrizione alla newsletter</h2>
      {known.length ? (
        <dl className="space-y-2 text-sm">
          {known.map(([key, label]) => (
            <Row key={key} label={label}>{utm?.[key]}</Row>
          ))}
        </dl>
      ) : (
        <p className="text-sm text-muted-foreground">
          {utm ? 'Iscritta, senza UTM registrati.' : 'Nessuna iscrizione con questo indirizzo.'}
        </p>
      )}
    </section>
  )
}

/** A short list of dated events («Ultimi accessi», «Download della guida»), newest
 *  first, with an empty state instead of nothing when there are none (REB-284). */
export function RecentEvents({
  title,
  empty,
  items,
}: {
  title: string
  empty: string
  items: { id: string; when: string }[]
}) {
  return (
    <section className="space-y-3 px-6 pb-6">
      <h2 className="text-sm font-medium">{title}</h2>
      {items.length ? (
        <ul className="space-y-1 text-sm">
          {items.map((item) => (
            <li key={item.id}>{formatDateTime(item.when)}</li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">{empty}</p>
      )}
    </section>
  )
}
