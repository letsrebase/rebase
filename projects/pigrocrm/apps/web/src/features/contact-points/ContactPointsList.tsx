import { Badge } from '@rebase/ui/badge'
import type { components } from '@/lib/api-types'
import { KIND_TEXT, LABEL_TEXT, pointHref } from './contactPoints'

type PointRead = components['schemas']['ContactPointRead']

/**
 * Every contact point of a person or a customer, in the order they are kept: the kind on the
 * left, and on the right the value as a link (`mailto:`, `tel:`, or the LinkedIn profile in a
 * new tab) with its label, its note and a marker on the primary one of its kind.
 */
export function ContactPointsList({ points }: { points: readonly PointRead[] }) {
  if (points.length === 0) {
    return <p className="py-2 text-muted-foreground">Nessun recapito.</p>
  }
  const ordered = [...points].sort((a, b) => a.position - b.position)
  return (
    <ul>
      {ordered.map((point) => {
        const href = pointHref(point)
        return (
          <li
            key={point.id}
            className="flex justify-between gap-4 border-b py-2 last:border-0"
            data-testid="contact-point"
          >
            <span className="text-muted-foreground">{KIND_TEXT[point.kind]}</span>
            <span className="min-w-0 space-y-1 text-right">
              {href === null ? (
                <span className="block break-words font-medium">{point.value}</span>
              ) : (
                <a
                  href={href}
                  className="block break-words font-medium underline underline-offset-2"
                  {...(point.kind === 'linkedin'
                    ? { target: '_blank', rel: 'noopener noreferrer' }
                    : {})}
                >
                  {point.value}
                </a>
              )}
              <span className="flex flex-wrap items-center justify-end gap-1">
                <Badge variant="outline">{LABEL_TEXT[point.label]}</Badge>
                {point.is_primary && <Badge variant="secondary">Principale</Badge>}
              </span>
              {point.note ? (
                <span className="block text-sm text-muted-foreground">{point.note}</span>
              ) : null}
            </span>
          </li>
        )
      })}
    </ul>
  )
}
