import { Link } from '@tanstack/react-router'
import type { Attivita } from '@/features/calendar/queries'
import { LINK_KIND_LABELS, linkOf, type LinkValue } from './links'
import { useAssignees, useRecordLabel } from './queries'

/** The record an attività hangs from, as a link to its own page. */
export function RecordLink({ link }: { link: LinkValue }) {
  const label = useRecordLabel(link)
  const text = (
    <>
      {LINK_KIND_LABELS[link.kind]} · {label ?? '…'}
    </>
  )
  const className = 'underline underline-offset-4'
  switch (link.kind) {
    case 'customer':
      return (
        <Link to="/app/customers/$customerId" params={{ customerId: link.id }} className={className}>
          {text}
        </Link>
      )
    case 'person':
      return (
        <Link to="/app/people/$personId" params={{ personId: link.id }} className={className}>
          {text}
        </Link>
      )
    case 'deal':
      return (
        <Link to="/app/deal/$dealId" params={{ dealId: link.id }} className={className}>
          {text}
        </Link>
      )
    case 'invoice':
      return (
        <Link to="/app/invoices/$invoiceId" params={{ invoiceId: link.id }} className={className}>
          {text}
        </Link>
      )
  }
}

/**
 * The two facts about an attività that its title does not carry: who it is for and what it
 * is about. Nothing is drawn for a task that has neither, so the plain ones stay plain.
 * `hideLink` is for a record's own page, where the link would point at the page it is on.
 */
export function AttivitaMeta({
  attivita,
  hideLink = false,
}: {
  attivita: Attivita
  hideLink?: boolean
}) {
  const assignees = useAssignees()
  const name = assignees.nameOf(attivita.assegnata_a)
  const link = hideLink ? null : linkOf(attivita)
  if (name === null && link === null) return null
  return (
    <span className="flex flex-wrap items-center gap-x-3 text-xs text-muted-foreground">
      {name !== null && <span>Per {attivita.assegnata_a === assignees.me ? 'me' : name}</span>}
      {link !== null && <RecordLink link={link} />}
    </span>
  )
}
