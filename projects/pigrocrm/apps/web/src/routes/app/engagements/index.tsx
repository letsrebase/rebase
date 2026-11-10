import { createFileRoute } from '@tanstack/react-router'
import { EngagementsPage } from '@/features/engagements/EngagementsPage'
import { parseEngagementsSearch } from '@/features/engagements/search'

export const Route = createFileRoute('/app/engagements/')({
  component: EngagementsPage,
  // The tab and the filters travel in the URL, so a bookmark or a link from the registry lands on
  // the same view.
  validateSearch: parseEngagementsSearch,
})
