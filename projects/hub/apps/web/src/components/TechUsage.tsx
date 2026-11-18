import { useId } from 'react'
import { Badge } from '@rebase/ui/badge'
import { cn } from '@rebase/ui/cn'
import {
  BAND_FILLS,
  USAGE_LEGEND,
  sortByUsage,
  usageCounts,
  usageHeadline,
  type Technology,
} from '@/lib/techUsage'

/**
 * One technology as a badge whose background fills from the left like a progress bar
 * (REB-1130): a third for an occasional presence, two thirds for a recurring one, all of it
 * for a continuous one. The fill shows the band and nothing else, and the band measures how
 * often and how steadily the technology appears in the person's work, never how well they
 * know it.
 *
 * The text on the badge is the name. The band in words and the counts are in the accessible
 * name (read out after the name) and in a tooltip on hover and keyboard focus; the badge is
 * focusable for that. With `tooltip` off (a row of a table, whose scroll container would clip
 * it) they are a native `title` instead, so the hover still says them.
 */
export function UsageBadge({ technology, tooltip = true }: { technology: Technology; tooltip?: boolean }) {
  const headline = usageHeadline(technology)
  const counts = usageCounts(technology)
  return (
    <li className="group/usage relative">
      <Badge
        variant="meter"
        fill={BAND_FILLS[technology.band]}
        tabIndex={tooltip ? 0 : undefined}
        // Escape closes the tooltip of a focused badge (WCAG 1.4.13): blur takes it out of the group's focus.
        onKeyDown={tooltip ? (event) => event.key === 'Escape' && event.currentTarget.blur() : undefined}
        title={tooltip ? undefined : `${headline}: ${counts}`}
        data-band={technology.band}
        className="relative h-6 px-2.5 text-sm"
      >
        {technology.name}
        <span className="sr-only">{`, ${headline.toLowerCase()}: ${counts}`}</span>
      </Badge>
      {tooltip && (
        <span
          aria-hidden="true"
          className="absolute top-full left-0 z-10 mt-1 hidden w-max max-w-64 border bg-popover px-2.5 py-1.5 text-xs text-popover-foreground shadow-md group-focus-within/usage:block group-hover/usage:block"
        >
          <span className="block font-medium">{headline}</span>
          <span className="block text-muted-foreground">{counts}</span>
        </span>
      )}
    </li>
  )
}

/**
 * The technologies of a profile as a wrapping row of `UsageBadge`s, strongest band first and
 * then the more months, with the one-line legend that says what the fill means. Nothing at all
 * for a profile with none, so a caller needs no empty state of its own.
 *
 * `limit` shows the first N and a «+M» for the rest, for a row of a list; it implies the
 * compact form (no tooltip, no legend), since a list repeats the badge once per row and
 * says what it means once, in its own header.
 */
export function UsageBadges({
  technologies,
  limit,
  className,
}: {
  technologies: readonly Technology[]
  limit?: number
  className?: string
}) {
  const legendId = useId()
  if (technologies.length === 0) return null
  const sorted = sortByUsage(technologies)
  const compact = limit !== undefined
  const shown = compact ? sorted.slice(0, limit) : sorted
  const rest = sorted.length - shown.length
  return (
    <div className={cn('space-y-1.5', className)}>
      <ul aria-label="Tecnologie" aria-describedby={compact ? undefined : legendId} className="flex flex-wrap gap-1.5">
        {shown.map((technology) => (
          <UsageBadge key={technology.name} technology={technology} tooltip={!compact} />
        ))}
        {rest > 0 && (
          <li className="self-center text-xs text-muted-foreground">
            <span aria-hidden="true">+{rest}</span>
            <span className="sr-only">{`e altre ${rest}`}</span>
          </li>
        )}
      </ul>
      {!compact && (
        <p id={legendId} className="text-xs text-muted-foreground">
          {USAGE_LEGEND}
        </p>
      )}
    </div>
  )
}
