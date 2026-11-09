import { useNavigate } from '@tanstack/react-router'
import { Building2, Mail, Plus, Search } from 'lucide-react'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { DataTable } from '@/components/DataTable'
import { FilterRow } from '@/components/FilterRow'
import { LoadMore } from '@/components/LoadMore'
import { PageHeader } from '@/components/PageHeader'
import { Button } from '@rebase/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { OrganizationForm } from '@/features/organizations/OrganizationForm'
import { buildOrganizationColumns } from '@/features/organizations/columns'
import { useCreateOrganization, useOrganizationPages } from '@/features/organizations/queries'
import { useGmailHealth } from '@/features/gmail/queries'
import { SuggestedOrganizations } from '@/features/gmail/SuggestedOrganizations'
import { SEARCH_DEBOUNCE_MS } from '@/features/search/queries'
import { useDebounced } from '@/hooks/useDebounced'
import { useLegalEntitiesToName } from '@/lib/legalEntity'
import { toProblem, type ProblemDetail } from '@/lib/api'
import { useCanWrite } from '@/lib/auth'
import { useEntitySchema } from '@/lib/schema'

/**
 * Exported so `index.test.tsx` can render the list without a router. The route
 * component below is what reads `?search=` out of the URL; this is what draws the page.
 */
export function OrganizationsPage({ initialSearch }: { initialSearch: string }) {
  const navigate = useNavigate()
  const canWrite = useCanWrite()
  const [search, setSearch] = useState(initialSearch)
  const [open, setOpen] = useState(false)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const [proposing, setProposing] = useState(false)
  // A connected mailbox whose consent still holds: the only one that can propose.
  const mailbox = useGmailHealth().data?.account?.status === 'active'

  const schema = useEntitySchema('organization')
  // The request follows the settled term, not every keystroke.
  const settled = useDebounced(search, SEARCH_DEBOUNCE_MS)
  const organizations = useOrganizationPages({ search: settled || undefined })
  const create = useCreateOrganization()

  // Recomputed every render, not memoised: `schema.data?.custom_fields` only
  // changes when the schema query itself refetches, and `buildOrganizationColumns`
  // does no work heavier than building a handful of plain objects -- not worth a
  // `useMemo` whose dependency array would just repeat the same query result.
  // «Azienda» only under «Tutte le aziende» from the second azienda on (REB-625).
  const columns = buildOrganizationColumns(schema.data?.custom_fields ?? [], {
    legal_entities: useLegalEntitiesToName(),
  })

  return (
    <>
      <PageHeader
        icon={Building2}
        title="Clienti"
        actions={
          canWrite && (
            <>
              {mailbox ? (
                <Button variant="outline" onClick={() => setProposing(true)}>
                  <Mail className="mr-2 size-4" />
                  Proponi dalla casella
                </Button>
              ) : null}
              <Button
                onClick={() => {
                  setProblem(null)
                  setOpen(true)
                }}
              >
                <Plus className="mr-2 size-4" />
                Nuovo cliente
              </Button>
            </>
          )
        }
      >
        {/* No state chips: a customer has no state to filter by. The search box is the
            one filter this list has ever had, and the revision moves it into the row
            rather than inventing one to keep it company. */}
        <FilterRow>
          <div className="relative w-full max-w-sm">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pl-9"
              placeholder="Cerca per ragione sociale, P.IVA, codice fiscale o email…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
        </FilterRow>
      </PageHeader>

      <div className="px-8 pb-8">
        <DataTable
          label="Clienti"
          columns={columns}
          data={organizations.items}
          isLoading={organizations.isLoading}
          isError={organizations.isError}
          error={organizations.error}
          onRowClick={(row) =>
            void navigate({ to: '/app/organizations/$organizationId', params: { organizationId: row.id } })
          }
          emptyMessage="Nessun cliente. Creane uno per iniziare."
        />
        {organizations.hasMore && (
          <LoadMore
            label="Carica altri"
            message={`Mostrati ${organizations.items.length} clienti, ce ne sono altri.`}
            isFetchingMore={organizations.isFetchingMore}
            hasError={organizations.isError}
            error={organizations.error}
            onLoadMore={organizations.loadMore}
          />
        )}
      </div>

      {/* The Gmail proposals (REB-223), for whoever connects the mailbox after the space
          already has work in it and so never sees the Home's door. Mounted only while
          open: each read asks Gmail about a year of sent mail. */}
      <Dialog open={proposing} onOpenChange={setProposing}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Proponi dalla casella</DialogTitle>
          </DialogHeader>
          {proposing ? <SuggestedOrganizations onImported={() => setProposing(false)} /> : null}
        </DialogContent>
      </Dialog>

      <OrganizationForm
        title="Nuovo cliente"
        open={open}
        onOpenChange={setOpen}
        customFields={schema.data?.custom_fields ?? []}
        problem={problem}
        busy={create.isPending}
        onSubmit={(values) => {
          setProblem(null)
          create.mutate(values, {
            onSuccess: () => {
              setOpen(false)
              toast.success('Cliente creato')
            },
            onError: (error) => setProblem(toProblem(error)),
          })
        }}
      />
    </>
  )
}
