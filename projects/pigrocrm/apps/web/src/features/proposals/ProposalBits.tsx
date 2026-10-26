import { Link } from '@tanstack/react-router'
import { Badge } from '@rebase/ui/badge'
import { StatusPill, type StatusTone } from '@/components/StatusPill'
import { useCustomer } from '@/features/customers/queries'
import {
  BAND_LABELS,
  STATO_LABELS,
  TARGET_LABELS,
  confidenceBand,
  formatConfidence,
  type Proposal,
  type ProposalDescription,
} from './fields'
import { useProposalContract } from './queries'

/**
 * The tone each state reads as. `in_attesa` is gold: in this product gold means «waiting
 * on somebody», and a proposal in the queue is exactly that. An accepted one is the
 * settled, ordinary outcome (`ink`), a refused one the one that went the other way.
 */
const STATO_TONE: Record<string, StatusTone> = {
  in_attesa: 'gold',
  accettata: 'ink',
  rifiutata: 'danger',
}

export function ProposalStatoPill({ stato }: { stato: string }) {
  return <StatusPill tone={STATO_TONE[stato] ?? 'muted'}>{STATO_LABELS[stato] ?? stato}</StatusPill>
}

export function TargetBadge({ targetType }: { targetType: string }) {
  return <Badge variant="outline">{TARGET_LABELS[targetType] ?? targetType}</Badge>
}

/**
 * The agent's own declared confidence and, when it gave one, the reason. The band is the
 * word and the percentage the evidence: «Confidenza bassa (42%)» tells a reviewer where
 * to look first, where «0,42» alone says nothing.
 */
export function Confidence({ proposal, showReason = false }: { proposal: Proposal; showReason?: boolean }) {
  const band = confidenceBand(proposal.confidenza)
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
      <StatusPill tone={band === 'alta' ? 'ink' : band === 'media' ? 'gold' : 'danger'}>
        {BAND_LABELS[band]} ({formatConfidence(proposal.confidenza)})
      </StatusPill>
      {showReason && proposal.motivo_confidenza ? (
        <span className="text-sm text-muted-foreground">{proposal.motivo_confidenza}</span>
      ) : null}
    </span>
  )
}

/**
 * Who the proposal is about: the customer of a contract, or the contract a day is for.
 * Read from the id the fields name, so a row says «Acme S.r.l.» and not a uuid. While the
 * name is on its way, or when it cannot be read, nothing is drawn: the title already
 * stands on its own, and a placeholder would read as a missing record.
 */
export function ProposalSubject({
  description,
  link = false,
  prefix = '',
}: {
  description: ProposalDescription
  link?: boolean
  /** Words that belong to the name and so vanish with it: «Per il cliente ». */
  prefix?: string
}) {
  const customer = useCustomer(description.customerId ?? '', { enabled: description.customerId !== null })
  const contract = useProposalContract(description.contractId)
  if (description.customerId !== null && customer.data) {
    const name = customer.data.ragione_sociale
    return link ? (
      <>
        {prefix}
        <Link
          to="/app/customers/$customerId"
          params={{ customerId: description.customerId }}
          className="text-foreground underline underline-offset-4"
        >
          {name}
        </Link>
      </>
    ) : (
      <>
        {prefix}
        {name}
      </>
    )
  }
  if (description.contractId !== null && contract.data) {
    return (
      <>
        {prefix}
        {contract.data.titolo}
      </>
    )
  }
  return null
}
