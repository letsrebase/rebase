import { createFileRoute, useParams } from '@tanstack/react-router'
import { ProposalDetail } from '@/features/proposals/ProposalDetail'

function ProposalRoute() {
  const { proposalId } = useParams({ from: '/app/proposals/$proposalId' })
  return <ProposalDetail proposalId={proposalId} />
}

export const Route = createFileRoute('/app/proposals/$proposalId')({
  component: ProposalRoute,
})
