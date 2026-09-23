import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { Building2, Mail, Plus, Search } from 'lucide-react'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { DataTable } from '@/components/DataTable'
import { FilterRow } from '@/components/FilterRow'
import { PageHeader } from '@/components/PageHeader'
import { Button } from '@rebase/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { CustomerForm } from '@/features/customers/CustomerForm'
import { buildCustomerColumns } from '@/features/customers/columns'
import { useCreateCustomer, useCustomers } from '@/features/customers/queries'
import { useGmailHealth } from '@/features/gmail/queries'
import { SuggestedCustomers } from '@/features/gmail/SuggestedCustomers'
import { toProblem, type ProblemDetail } from '@/lib/api'
import { useCanWrite } from '@/lib/auth'
import { useEntitySchema } from '@/lib/schema'

/**
 * Exported so `index.test.tsx` can render the list without a router. The route
 * component below is what reads `?search=` out of the URL; this is what draws the page.
 */
export function CustomersPage({ initialSearch }: { initialSearch: string }) {
  const navigate = useNavigate()
  const canWrite = useCanWrite()
  const [search, setSearch] = useState(initialSearch)
  const [open, setOpen] = useState(false)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const [proposing, setProposing] = useState(false)
  // A connected mailbox whose consent still holds: the only one that can propose.
  const mailbox = useGmailHealth().data?.account?.status === 'active'

  const schema = useEntitySchema('customer')
  const customers = useCustomers({ search: search || undefined })
  const create = useCreateCustomer()

  // Recomputed every render, not memoised: `schema.data?.custom_fields` only
  // changes when the schema query itself refetches, and `buildCustomerColumns`
  // does no work heavier than building a handful of plain objects -- not worth a
  // `useMemo` whose dependency array would just repeat the same query result.
  const columns = buildCustomerColumns(schema.data?.custom_fields ?? [])

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
          columns={columns}
          data={customers.data?.items ?? []}
          isLoading={customers.isLoading}
          isError={customers.isError}
          error={customers.error}
          onRowClick={(row) =>
            void navigate({ to: '/app/customers/$customerId', params: { customerId: row.id } })
          }
          emptyMessage="Nessun cliente. Creane uno per iniziare."
        />
      </div>

      {/* The Gmail proposals (REB-223), for whoever connects the mailbox after the space
          already has work in it and so never sees the Home's door. Mounted only while
          open: each read asks Gmail about a year of sent mail. */}
      <Dialog open={proposing} onOpenChange={setProposing}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Proponi dalla casella</DialogTitle>
          </DialogHeader>
          {proposing ? <SuggestedCustomers onImported={() => setProposing(false)} /> : null}
        </DialogContent>
      </Dialog>

      <CustomerForm
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

/**
 * The `?search=` term is an entry point, not a live mirror of the box. The palette's
 * «vedi tutti» links here carrying the term the user searched for, and `key` makes
 * arriving with a *different* term a remount, so the filter is seeded even when this
 * route is already open. Typing afterwards stays local: pushing every keystroke through
 * the router would make a controlled input wait on a navigation to echo the character
 * back, which is how a fast typist loses characters.
 */
function CustomersRoute() {
  const { search } = Route.useSearch()
  return <CustomersPage key={search ?? ''} initialSearch={search ?? ''} />
}

export const Route = createFileRoute('/app/customers/')({
  component: CustomersRoute,
  // Declared so the palette can link here with a term (`navigate({ to, search })` is
  // typed against this). An empty or non-string value is dropped rather than carried as
  // `?search=`, so the URL never claims a filter that is not applied.
  validateSearch: (search: Record<string, unknown>): { search?: string } => ({
    search:
      typeof search.search === 'string' && search.search.length > 0 ? search.search : undefined,
  }),
})
