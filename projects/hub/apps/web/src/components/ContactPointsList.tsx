import type { ContactPoint } from '@/lib/api'
import { CONTACT_KIND_NAMES, CONTACT_KINDS, pointSuffix } from '@/lib/contactPoints'
import { WebLink } from '@/components/WebLink'

/**
 * A person's contact points as a read-only block (REB-944): one line per point, grouped by
 * kind, the sign-in address first, each with its label and note and the primary one of a
 * kind marked. An address is a `mailto:`, a phone a `tel:`, a profile opens on LinkedIn.
 */
export function ContactPointsList({ points }: { points: readonly ContactPoint[] }) {
  const kinds = CONTACT_KINDS.filter((kind) => points.some((point) => point.kind === kind))
  if (kinds.length === 0) return <span>—</span>
  return (
    <ul className="space-y-1.5">
      {kinds.flatMap((kind) =>
        points
          .filter((point) => point.kind === kind)
          .map((point) => (
            <li key={`${point.kind}-${point.id ?? 'login'}-${point.value}`} className="break-words">
              <span className="font-normal text-muted-foreground">{CONTACT_KIND_NAMES[kind]}: </span>
              <PointValue point={point} />
              {point.is_primary && (
                <span className="ml-2 text-xs font-normal text-muted-foreground">principale</span>
              )}
            </li>
          )),
      )}
    </ul>
  )
}

function PointValue({ point }: { point: ContactPoint }) {
  const suffix = pointSuffix(point)
  if (point.kind === 'email') {
    return (
      <>
        <a className="underline underline-offset-2" href={`mailto:${point.value}`}>
          {point.value}
        </a>
        {suffix}
      </>
    )
  }
  if (point.kind === 'phone') {
    return (
      <>
        <a className="underline underline-offset-2" href={`tel:${point.value.replace(/[^\d+]/g, '')}`}>
          {point.value}
        </a>
        {suffix}
      </>
    )
  }
  return (
    <>
      <WebLink href={point.value} />
      {suffix}
    </>
  )
}
