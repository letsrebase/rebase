import { useNavigate } from '@tanstack/react-router'
import { Building2, Mail, Plus, Search, Store, type LucideIcon } from 'lucide-react'
import { useState } from 'react'
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
import type { RegistryPage } from '@/features/organizations/search'
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

/** The words of each page, in one place: its title and sentence, the button that creates, what an
 *  empty list says, what a created record is called. `icon` is the page's and the sidebar's. */
const VIEWS: Record<
  RegistryPage,
  {
    title: string
    /** Said under the title; the sentence about the other list only while that list exists. */
    description: (purchasesOn: boolean) => string
    icon: LucideIcon
    create: string
    created: string
    empty: string
    plural: string
    roles: string[]
  }
> = {
  customer: {
    title: 'Clienti',
    description: (purchasesOn) =>
      purchasesOn
        ? 'Le aziende per cui lavori. Chi è anche fornitore compare anche in Fornitori.'
        : 'Le aziende per cui lavori.',
    icon: Building2,
    create: 'Nuovo cliente',
    created: 'Cliente creato',
    empty: 'Nessun cliente. Creane uno per iniziare.',
    plural: 'clienti',
    roles: ['customer'],
  },
  supplier: {
    title: 'Fornitori',
    description: () => 'Le aziende da cui compri. Chi è anche cliente compare anche in Clienti.',
    icon: Store,
    create: 'Nuovo fornitore',
    created: 'Fornitore creato',
    empty: 'Nessun fornitore. Compaiono quando registri una fattura passiva o ne crei uno.',
    plural: 'fornitori',
    roles: ['supplier'],
  },
}

/**
 * «Clienti» and «Fornitori»: one page each over the one registry of organizations (REB-1053,
 * which reversed the single «Anagrafiche» page of REB-996). Each has its own title, sentence,
 * strong button and list, with no tab between them; an organization that plays both roles is on
 * both lists, and its own page is the same either way. The pages differ by `role` alone, which the
 * API filters: this is the one component, not two copies.
 *
 * `/app/suppliers` is the `purchases` module's (`ROUTE_MODULES`), so the layout's guard turns a
 * stale link away before this renders.
 *
 * Exported so `OrganizationsPage.test.tsx` can render the list without a router. The route
 * component is what reads `?search=` out of the URL; this is what draws the page.
 */
export function OrganizationsPage({
  initialSearch,
  role,
}: {
  initialSearch: string
  role: RegistryPage
}) {
  const purchasesOn = useModules().on('purchases')
  return <RegistryList initialSearch={initialSearch} role={role} purchasesOn={purchasesOn} />
}

function RegistryList({
  initialSearch,
  role,
  purchasesOn,
}: {
  initialSearch: string
  role: RegistryPage
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
      <PageHeader
        icon={view.icon}
        title={view.title}
        description={view.description(purchasesOn)}
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
      >
        {/* The search box, and on «Fornitori» «Tipo fiscale», which the API filters. */}
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
      </PageHeader>

      <div className="px-8 pb-8">
        {empty ? (
          <div className="border border-dashed px-6 py-12 text-center">
            <p className="mx-auto max-w-md text-sm text-muted-foreground">
              {!narrowed
                ? view.empty
                : supplierKind !== undefined && settled === ''
                  ? 'Nessun fornitore con questo tipo fiscale.'
                  : `Nessun risultato per «${settled}».`}
            </p>
            {canWrite && !narrowed ? <div className="mt-4">{createButton}</div> : null}
          </div>
        ) : wide ? (
          <DataTable
            label={view.title}
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
