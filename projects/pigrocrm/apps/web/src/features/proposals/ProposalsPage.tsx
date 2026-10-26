import { Link } from '@tanstack/react-router'
import { ChevronRight, Inbox } from 'lucide-react'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { PageHeader } from '@/components/PageHeader'
import { Skeleton } from '@rebase/ui/skeleton'
import { Tabs, TabsList, TabsTrigger } from '@rebase/ui/tabs'
import { formatOccurredAt } from '@/components/activityLabels'
import { Confidence, ProposalStatoPill, ProposalSubject, TargetBadge } from './ProposalBits'
import {
  STATI,
  decisionLine,
  describeProposal,
  excerptPreview,
  type Proposal,
  type ProposalStato,
} from './fields'
import { QUEUE_LIMIT, useProposals } from './queries'

const EMPTY: Record<ProposalStato, { title: string; body: string }> = {
  in_attesa: {
    title: 'Niente da rivedere',
    body: 'Quando un assistente propone un contratto o una giornata letti da un documento, li trovi qui per confermarli o rifiutarli.',
  },
  accettata: {
    title: 'Nessuna proposta accettata',
    body: 'Le proposte che confermi compaiono qui, con il contratto o la giornata che hanno creato.',
  },
  rifiutata: {
    title: 'Nessuna proposta rifiutata',
    body: 'Le proposte che rifiuti restano qui, con il motivo.',
  },
}

/**
 * The Proposte queue (REB-750): what agents proposed from a document and nobody has
 * decided yet, oldest first because a queue is worked in the order things arrived, and the
 * two decided lists behind their own tabs, newest decision first.
 *
 * The tab is in the URL (`?stato=`) for the reason the Home's period is: a link to «le
 * rifiutate» reopens the same list. Deciding happens on the detail page, where the source
 * excerpt and every proposed field sit next to the buttons; a row here is a way to get
 * there, never a one-click accept of something the reader has not seen.
 */
export function ProposalsPage({
  stato,
  onStatoChange,
}: {
  stato: ProposalStato
  onStatoChange: (next: ProposalStato) => void
}) {
  const proposals = useProposals(stato)
  const pending = useProposals('in_attesa')
  // The API orders each list (the queue by arrival, the decided ones by decision, newest
  // first), so what comes back is what is drawn.
  const rows = proposals.data?.items ?? []
  const waiting = pending.data?.items.length

  return (
    <Tabs value={stato} onValueChange={(next) => onStatoChange(next as ProposalStato)}>
      <PageHeader
        icon={Inbox}
        title="Proposte"
        description="Quello che gli assistenti hanno letto dai documenti e aspetta la tua conferma."
        tabs={
          <TabsList variant="line">
            {STATI.map((candidate) => (
              <TabsTrigger key={candidate.id} value={candidate.id}>
                {candidate.label}
                {candidate.id === 'in_attesa' && waiting ? (
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
          <div className="space-y-3" role="status" aria-label="Caricamento proposte">
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-24 w-full" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <div className="border border-dashed px-6 py-12 text-center">
            <h2 className="font-semibold">{EMPTY[stato].title}</h2>
            <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">{EMPTY[stato].body}</p>
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
  const decided = proposal.stato !== 'in_attesa'
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
            «{excerptPreview(proposal.estratto)}»
          </p>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            {decided ? <ProposalStatoPill stato={proposal.stato} /> : <Confidence proposal={proposal} />}
            <span>
              {decided && proposal.deciso_il
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
