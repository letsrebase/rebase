import { Plus } from 'lucide-react'
import { useState } from 'react'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { FreelancerAvailability } from '@/features/engagements/AvailabilityStrip'
import { EngagementFormDialog } from '@/features/engagements/EngagementFormDialog'
import { EngagementsTable } from '@/features/engagements/EngagementsTable'
import { useEngagementList } from '@/features/engagements/queries'

/**
 * The «Incarichi» tab of a freelancer on the registry (REB-1035): their availability over the
 * coming weeks above the list of every engagement rebase holds for them, and «Nuovo incarico»
 * with the freelancer already chosen and locked. The caller draws it only for an administrator,
 * for a freelancer, while the `collaborators` module is on.
 */
export function FreelancerEngagements({ freelancerId }: { freelancerId: string }) {
  const list = useEngagementList(null, { freelancerId })
  const [creating, setCreating] = useState(false)

  return (
    <div className="space-y-6">
      <section className="space-y-3">
        <h2 className="font-semibold">Disponibilità</h2>
        <FreelancerAvailability freelancerId={freelancerId} />
      </section>

      <section className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-semibold">Incarichi</h2>
          <Button onClick={() => setCreating(true)}>
            <Plus className="mr-2 size-4" />
            Nuovo incarico
          </Button>
        </div>

        {list.isLoading ? (
          <LoadingState label="Leggo gli incarichi…" />
        ) : list.isError && list.items.length === 0 ? (
          <QueryErrorBanner error={list.error} onRetry={list.refetch} />
        ) : list.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nessun incarico per questo freelance.</p>
        ) : (
          <EngagementsTable engagements={list.items} showFreelancer={false} />
        )}

        {list.hasMore && (
          <div className="flex flex-col gap-2">
            <div>
              <Button variant="outline" size="sm" onClick={list.fetchMore} disabled={list.isFetchingMore}>
                {list.isFetchingMore ? 'Caricamento…' : 'Mostra altri'}
              </Button>
            </div>
            {list.isError && <QueryErrorBanner error={list.error} />}
          </div>
        )}
      </section>

      <EngagementFormDialog open={creating} onOpenChange={setCreating} freelancerId={freelancerId} />
    </div>
  )
}
