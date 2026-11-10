import { useNavigate } from '@tanstack/react-router'
import { Building2, Mail, Plus, Search } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { toast } from '@rebase/ui/sonner'
import { DataTable } from '@/components/DataTable'
import { FilterRow } from '@/components/FilterRow'
import { LoadMore } from '@/components/LoadMore'
import { LoadingState } from '@/components/LoadingState'
import { PageHeader } from '@/components/PageHeader'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Tabs, TabsList, TabsTrigger } from '@rebase/ui/tabs'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import { OrganizationForm } from '@/features/organizations/OrganizationForm'
import { RoleChips } from '@/features/organizations/RoleChips'
import { buildOrganizationColumns } from '@/features/organizations/columns'
import {
  useCreateOrganization,
  useOrganizationCount,
  useOrganizationPages,
  type Organization,
} from '@/features/organizations/queries'
import {
  SUPPLIER_KIND_LABELS,
  SUPPLIER_KINDS,
  supplierKindText,
  supplierTermsShort,
  type SupplierKind,
} from '@/features/organizations/roles'
import type { RegistryRole } from '@/features/organizations/search'
import { useGmailHealth } from '@/features/gmail/queries'
import { SuggestedOrganizations } from '@/features/gmail/SuggestedOrganizations'
import { SEARCH_DEBOUNCE_MS } from '@/features/search/queries'
import { useDebounced } from '@/hooks/useDebounced'
import { useLegalEntitiesToName } from '@/lib/legalEntity'
import { useModules } from '@/lib/modules'
import { toProblem, type ProblemDetail } from '@/lib/api'
import { useCanWrite } from '@/lib/auth'
import { useEntitySchema } from '@/lib/schema'

/** What the «Tipo fiscale» select submits for «Tutti i tipi»: a `Select` item cannot hold `null`. */
const ALL_KINDS = '__tutti'

/** The viewport from which the registry is a table; below it, one column of cards. */
const TABLE = '(min-width: 768px)'

/** The words of each view, in one place: the button that creates, what an empty list says,
 *  what a created record is called. The brief's sample copy, verbatim where it gives it. */
const VIEWS: Record<
  RegistryRole,
  { tab: string; create: string; created: string; empty: string; plural: string; roles: string[] }
> = {
  any: {
    tab: 'Tutte',
    create: 'Nuova anagrafica',
    created: 'Anagrafica creata',
    empty: 'Nessuna anagrafica. Creane una per iniziare.',
    plural: 'anagrafiche',
    roles: ['customer'],
  },
  customer: {
    tab: 'Clienti',
    create: 'Nuovo cliente',
    created: 'Cliente creato',
    empty: 'Nessun cliente. Creane uno per iniziare.',
    plural: 'clienti',
    roles: ['customer'],
  },
  supplier: {
    tab: 'Fornitori',
    create: 'Nuovo fornitore',
    created: 'Fornitore creato',
    empty: 'Nessun fornitore. Compaiono quando registri una fattura passiva o ne crei uno.',
    plural: 'fornitori',
    roles: ['supplier'],
  },
}

/**
 * «Anagrafiche»: the registry of every organization the space deals with, one page with a
 * view per role (`?role=`), the approved brief of 2026-11-10. «Clienti» in «Vendite» and
 * «Fornitori» in «Amministrazione» open it on those tabs. A «Fornitori» view with the
 * `purchases` module off is not drawn: its tab is gone and a stale link says why.
 *
 * Exported so `index.test.tsx` can render the list without a router. The route
 * component is what reads `?search=` and `?role=` out of the URL; this is what draws the page.
 */
export function OrganizationsPage({
  initialSearch,
  role,
}: {
  initialSearch: string
  role: RegistryRole
}) {
  const purchasesOn = useModules().on('purchases')
  const suppliersOff = role === 'supplier' && !purchasesOn

  return suppliersOff ? (
    <Frame role={role} purchasesOn={false}>
      <div className="px-8 py-6">
        <p className="text-sm text-muted-foreground">
          Il modulo Acquisti è spento: un amministratore può accenderlo in Impostazioni → Moduli.
        </p>
      </div>
    </Frame>
  ) : (
    <RegistryList initialSearch={initialSearch} role={role} purchasesOn={purchasesOn} />
  )
}

/** The header every view shares: the title is «Anagrafiche» whatever the view, and the view is
 *  the active tab, never a second title. */
function Frame({
  role,
  purchasesOn,
  actions,
  filters,
  children,
}: {
  role: RegistryRole
  purchasesOn: boolean
  actions?: ReactNode
  filters?: ReactNode
  children: ReactNode
}) {
  const navigate = useNavigate()
  const views: RegistryRole[] = purchasesOn ? ['any', 'customer', 'supplier'] : ['any', 'customer']
  // The number beside each tab: the whole registry for that role. Absent until it arrives.
  const counts: Record<RegistryRole, number | undefined> = {
    any: useOrganizationCount('any'),
    customer: useOrganizationCount('customer'),
    supplier: useOrganizationCount('supplier', purchasesOn),
  }
  return (
    // The URL says the view: «Clienti» is the page with no `role`, the other two name it.
    <Tabs
      value={role}
      onValueChange={(next) =>
        void navigate({
          to: '/app/organizations',
          search: { role: next === 'customer' ? undefined : (next as RegistryRole) },
        })
      }
    >
      <PageHeader
        icon={Building2}
        title="Anagrafiche"
        description="Le aziende con cui lavori: clienti, fornitori o entrambi."
        actions={actions}
        tabs={
          <TabsList variant="line">
            {views.map((view) => (
              <TabsTrigger key={view} value={view}>
                {VIEWS[view].tab}
                {counts[view] !== undefined ? (
                  <span className="ml-2 tabular-nums text-muted-foreground">{counts[view]}</span>
                ) : null}
              </TabsTrigger>
            ))}
          </TabsList>
        }
      >
        {filters}
      </PageHeader>
      {children}
    </Tabs>
  )
}

function RegistryList({
  initialSearch,
  role,
  purchasesOn,
}: {
  initialSearch: string
  role: RegistryRole
  purchasesOn: boolean
}) {
  const navigate = useNavigate()
  const canWrite = useCanWrite()
  const wide = useMediaQuery(TABLE)
  const [search, setSearch] = useState(initialSearch)
  const [open, setOpen] = useState(false)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const [proposing, setProposing] = useState(false)
  // A connected mailbox whose consent still holds: the only one that can propose.
  const mailbox = useGmailHealth().data?.account?.status === 'active'

  const schema = useEntitySchema('organization')
  // The request follows the settled term, not every keystroke.
  const settled = useDebounced(search, SEARCH_DEBOUNCE_MS)
  // «Tipo fiscale» belongs to the supplier view; the other views never send it.
  const [kind, setKind] = useState<SupplierKind | null>(null)
  const supplierKind = role === 'supplier' ? (kind ?? undefined) : undefined
  const organizations = useOrganizationPages({
    search: settled || undefined,
    role,
    supplier_kind: supplierKind,
  })
  // The registry's whole count for this role: said in «Carica altri» when nothing narrows it.
  const total = useOrganizationCount(role)
  const narrowed = settled !== '' || supplierKind !== undefined
  const create = useCreateOrganization()
  const view = VIEWS[role]

  // Recomputed every render, not memoised: `schema.data?.custom_fields` only
  // changes when the schema query itself refetches, and `buildOrganizationColumns`
  // does no work heavier than building a handful of plain objects -- not worth a
  // `useMemo` whose dependency array would just repeat the same query result.
  // «Azienda» only under «Tutte le aziende» from the second azienda on (REB-625).
  const columns = buildOrganizationColumns(schema.data?.custom_fields ?? [], {
    legal_entities: useLegalEntitiesToName(),
    role,
  })

  function openOrganization(organization: Organization) {
    void navigate({
      to: '/app/organizations/$organizationId',
      params: { organizationId: organization.id },
    })
  }

  const empty =
    !organizations.isLoading && !organizations.isError && organizations.items.length === 0
  const createButton = (
    <Button
      onClick={() => {
        setProblem(null)
        setOpen(true)
      }}
    >
      <Plus className="mr-2 size-4" />
      {view.create}
    </Button>
  )

  return (
    <>
      <Frame
        role={role}
        purchasesOn={purchasesOn}
        actions={
          canWrite && (
            <>
              {mailbox && role === 'customer' ? (
                <Button variant="outline" onClick={() => setProposing(true)}>
                  <Mail className="mr-2 size-4" />
                  Proponi dalla casella
                </Button>
              ) : null}
              {createButton}
            </>
          )
        }
        // The search box, and on the supplier view «Tipo fiscale», which the API filters.
        filters={
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
            {role === 'supplier' ? (
              <Select
                value={kind ?? ALL_KINDS}
                onValueChange={(next) => setKind(next === ALL_KINDS ? null : (next as SupplierKind))}
              >
                <SelectTrigger aria-label="Tipo fiscale" className="w-full sm:w-52">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL_KINDS}>Tutti i tipi</SelectItem>
                  {SUPPLIER_KINDS.map((candidate) => (
                    <SelectItem key={candidate} value={candidate}>
                      {SUPPLIER_KIND_LABELS[candidate]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : null}
          </FilterRow>
        }
      >
        <div className="px-8 pb-8">
          {empty ? (
            <div className="border border-dashed px-6 py-12 text-center">
              <p className="mx-auto max-w-md text-sm text-muted-foreground">
                {settled === '' ? view.empty : `Nessun risultato per «${settled}».`}
              </p>
              {canWrite && settled === '' ? <div className="mt-4">{createButton}</div> : null}
            </div>
          ) : wide ? (
            <DataTable
              label={view.tab}
              columns={columns}
              data={organizations.items}
              isLoading={organizations.isLoading}
              loadingLabel="Leggo le anagrafiche…"
              isError={organizations.isError}
              error={organizations.error}
              onRetry={organizations.retry}
              onRowClick={openOrganization}
            />
          ) : (
            <OrganizationCards
              organizations={organizations.items}
              isLoading={organizations.isLoading}
              error={organizations.isError && organizations.items.length === 0 ? organizations.error : null}
              onRetry={organizations.retry}
              onOpen={openOrganization}
              purchasesOn={purchasesOn}
            />
          )}
          {organizations.hasMore && (
            <LoadMore
              label="Carica altri"
              message={
                total !== undefined && !narrowed
                  ? `Mostrati ${organizations.items.length} ${view.plural} su ${total}.`
                  : `Mostrati ${organizations.items.length} ${view.plural}, ce ne sono altri.`
              }
              isFetchingMore={organizations.isFetchingMore}
              hasError={organizations.isError}
              error={organizations.error}
              onLoadMore={organizations.loadMore}
            />
          )}
        </div>
      </Frame>

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
        title={view.create}
        open={open}
        onOpenChange={setOpen}
        customFields={schema.data?.custom_fields ?? []}
        defaultRoles={view.roles}
        problem={problem}
        busy={create.isPending}
        onOpenExisting={openOrganization}
        onSubmit={(values) => {
          setProblem(null)
          create.mutate(values, {
            onSuccess: () => {
              setOpen(false)
              toast.success(view.created)
            },
            onError: (error) => setProblem(toProblem(error)),
          })
        }}
      />
    </>
  )
}

/** The phone's registry: one column of cards, each with the name (which wraps), the roles, the
 *  fiscal kind and terms of a supplier and the contact. A card opens the organization. */
function OrganizationCards({
  organizations,
  isLoading,
  error,
  onRetry,
  onOpen,
  purchasesOn,
}: {
  organizations: Organization[]
  isLoading: boolean
  error: unknown
  onRetry: () => void
  onOpen: (organization: Organization) => void
  purchasesOn: boolean
}) {
  if (isLoading) return <LoadingState label="Leggo le anagrafiche…" className="py-8" />
  if (error) return <QueryErrorBanner error={error} onRetry={onRetry} />
  return (
    <ul className="grid grid-cols-1 gap-3">
      {organizations.map((organization) => {
        const contact = organization.email ?? organization.phone
        const asSupplier = purchasesOn && organization.roles.includes('supplier')
        return (
          <li key={organization.id}>
            <button
              type="button"
              onClick={() => onOpen(organization)}
              className="flex w-full flex-col gap-2 border border-border bg-card p-4 text-left outline-none hover:bg-muted/50 focus-visible:ring-[3px] focus-visible:ring-ring/50"
            >
              <span className="break-words font-medium">{organization.legal_name}</span>
              <span className="flex flex-wrap gap-1.5">
                <RoleChips roles={organization.roles} />
              </span>
              {asSupplier ? (
                <span className="text-sm text-muted-foreground">
                  {supplierKindText(organization)} · {supplierTermsShort(organization)}
                </span>
              ) : null}
              {contact ? <span className="break-all text-sm text-muted-foreground">{contact}</span> : null}
            </button>
          </li>
        )
      })}
    </ul>
  )
}
