import { Link } from '@tanstack/react-router'
import { ChevronRight, Inbox } from 'lucide-react'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { PageHeader } from '@/components/PageHeader'
import { LoadingState } from '@/components/LoadingState'
import { Tabs, TabsList, TabsTrigger } from '@rebase/ui/tabs'
import { formatOccurredAt } from '@/components/activityLabels'
import { Confidence, ProposalStatusPill, ProposalSubject, TargetBadge } from './ProposalBits'
import {
  Statuses,
  decisionLine,
  describeProposal,
  excerptPreview,
  type Proposal,
  type ProposalStatus,
} from './fields'
import { QUEUE_LIMIT, useProposals } from './queries'

const EMPTY: Record<ProposalStatus, { title: string; body: string }> = {
  pending: {
    title: 'Niente da rivedere',
    body: 'Quando un assistente propone un contratto o una giornata letti da un documento, li trovi qui per confermarli o rifiutarli.',
  },
  accepted: {
    title: 'Nessuna proposta accettata',
    body: 'Le proposte che confermi compaiono qui, con il contratto o la giornata che hanno creato.',
  },
  rejected: {
    title: 'Nessuna proposta rifiutata',
    body: 'Le proposte che rifiuti restano qui, con il motivo.',
  },
}

/**
 * The Proposte queue (REB-750): what agents proposed from a document and nobody has
 * decided yet, oldest first because a queue is worked in the order things arrived, and the
 * two decided lists behind their own tabs, newest decision first.
 *
 * The tab is in the URL (`?status=`) for the reason the Home's period is: a link to «le
 * rifiutate» reopens the same list. Deciding happens on the detail page, where the source
 * excerpt and every proposed field sit next to the buttons; a row here is a way to get
 * there, never a one-click accept of something the reader has not seen.
 */
export function ProposalsPage({
  status,
  onStatusChange,
}: {
  status: ProposalStatus
  onStatusChange: (next: ProposalStatus) => void
}) {
  const proposals = useProposals(status)
  const pending = useProposals('pending')
  // The API orders each list (the queue by arrival, the decided ones by decision, newest
  // first), so what comes back is what is drawn.
  const rows = proposals.data?.items ?? []
  const waiting = pending.data?.items.length

  return (
    <Tabs value={status} onValueChange={(next) => onStatusChange(next as ProposalStatus)}>
      <PageHeader
        icon={Inbox}
        title="Proposte"
        description="Quello che gli assistenti hanno letto dai documenti e aspetta la tua conferma."
        tabs={
          <TabsList variant="line">
            {Statuses.map((candidate) => (
              <TabsTrigger key={candidate.id} value={candidate.id}>
                {candidate.label}
                {candidate.id === 'pending' && waiting ? (
                  <span className="ml-2 tabular-nums text-muted-foreground">
                    {waiting >= QUEUE_LIMIT ? `${QUEUE_LIMIT}+` : waiting}
                  </span>
                ) : null}
              </TabsTrigger>
            ))}
          </TabsList>
        }
      />
      <div className="px-8 py-6">
        {proposals.isError ? (
          <QueryErrorBanner error={proposals.error} />
        ) : proposals.isLoading ? (
          <LoadingState label="Caricamento proposte…" />
        ) : rows.length === 0 ? (
          <div className="border border-dashed px-6 py-12 text-center">
            <h2 className="font-semibold">{EMPTY[status].title}</h2>
            <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">{EMPTY[status].body}</p>
          </div>
        ) : (
          <ul className="border-t">
            {rows.map((proposal) => (
              <ProposalRow key={proposal.id} proposal={proposal} />
            ))}
          </ul>
        )}
      </div>
    </Tabs>
  )
}

function ProposalRow({ proposal }: { proposal: Proposal }) {
  const description = describeProposal(proposal)
  const decided = proposal.status !== 'pending'
  return (
    <li className="border-b">
      <Link
        to="/app/proposals/$proposalId"
        params={{ proposalId: proposal.id }}
        className="group flex items-start gap-4 px-2 py-4 outline-none hover:bg-muted/50 focus-visible:ring-[3px] focus-visible:ring-ring/50"
      >
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <TargetBadge targetType={proposal.target_type} />
            <span className="font-medium">{description.title}</span>
            <span className="text-sm text-muted-foreground">
              <ProposalSubject description={description} />
            </span>
          </div>
          <p className="line-clamp-2 text-sm text-muted-foreground">
            «{excerptPreview(proposal.excerpt)}»
          </p>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            {decided ? <ProposalStatusPill status={proposal.status} /> : <Confidence proposal={proposal} />}
            <span>
              {decided && proposal.decided_at
                ? decisionLine(proposal)
                : `Arrivata ${formatOccurredAt(proposal.created_at)}`}
            </span>
          </div>
        </div>
        <ChevronRight className="mt-1 size-4 shrink-0 text-muted-foreground group-hover:text-foreground" aria-hidden />
      </Link>
    </li>
  )
}
