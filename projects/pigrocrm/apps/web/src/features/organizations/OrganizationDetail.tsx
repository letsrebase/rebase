import { useNavigate, useParams } from '@tanstack/react-router'
import { Building2, Pencil, Trash2 } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { toast } from '@rebase/ui/sonner'
import { renderFieldValue } from '@/components/DynamicFieldRenderer'
import { EntityDetailLayout } from '@/components/EntityDetailLayout'
import { EntityTask } from '@/features/tasks/EntityTask'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Card, CardContent } from '@rebase/ui/card'
import { LoadingState } from '@/components/LoadingState'
import { EconomicsTab } from '@/features/analytics/EconomicsTab'
import { ContactPointsList } from '@/features/contact-points/ContactPointsList'
import { OrganizationForm, organizationToFormValues } from '@/features/organizations/OrganizationForm'
import { displayNative } from '@/features/organizations/columns'
import { useOrganizationOwners } from '@/features/organizations/owner'
import {
  useOrganization,
  useOrganizationDeals,
  useOrganizationPeople,
  useDeleteOrganization,
  useUpdateOrganization,
  type Organization,
  type RelatedDeal,
  type RelatedPerson,
} from '@/features/organizations/queries'
import { formatDealValue } from '@/features/deals/columns'
import { ContractsTab } from '@/features/contracts/ContractsTab'
import { DocumentsTab } from '@/features/documents/DocumentsTab'
import { EmailTab } from '@/features/gmail/EmailTab'
import { useGmailConfigured } from '@/features/gmail/queries'
import { InvoicesTab } from '@/features/invoices/InvoicesTab'
import { NewProformaButton } from '@/features/invoices/NewProformaDialog'
import { NotesSection } from '@/features/notes/NotesSection'
import { toProblem, type ProblemDetail } from '@/lib/api'
import { useCanWrite } from '@/lib/auth'
import { useModules } from '@/lib/modules'
import { useEntitySchema } from '@/lib/schema'

const EMPTY = '—'

/**
 * The terms every due date to this customer is computed from (REB-326), in the words a
 * contract uses: «30 giorni data fattura fine mese». Days absent means the fiscal
 * profile's number applies at emission, and the row says so rather than printing a
 * dash, since a dash would read as "no terms" on a customer that does have a rule.
 */
function paymentTerms(customer: Organization): string {
  const endMonth = customer.payment_end_of_month ? ' fine mese' : ''
  if (customer.payment_days === null || customer.payment_days === undefined) {
    return `Dal profilo fiscale${endMonth ? `,${endMonth}` : ''}`
  }
  return `${customer.payment_days} giorni data fattura${endMonth}`
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 border-b py-2 last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-right font-medium">{value}</span>
    </div>
  )
}

/** A short preview list, not a paginated table: this tab exists so a customer's
 *  linked people/deals are visible at all here, not to replace the full Persone/
 *  Deal screens (`routes/app/people`, `routes/app/deal`). Loading, error and
 *  empty are still drawn as three different things -- the same reason
 *  DataTable/Timeline never collapse them -- since a stalled request and a
 *  genuinely empty list are different claims even in a small card. */
function LinkedList<T>({
  query,
  emptyMessage,
  renderItem,
}: {
  query: { data?: { items: T[] }; isLoading: boolean; isError: boolean; error: unknown }
  emptyMessage: string
  renderItem: (item: T) => ReactNode
}) {
  if (query.isLoading) {
    return <LoadingState />
  }
  if (query.isError) {
    return <p className="text-sm text-destructive">{toProblem(query.error).detail}</p>
  }
  const items = query.data?.items ?? []
  if (items.length === 0) {
    return <p className="text-muted-foreground">{emptyMessage}</p>
  }
  return <div>{items.map(renderItem)}</div>
}

export function OrganizationDetail() {
  const { organizationId: customerId } = useParams({ from: '/app/organizations/$organizationId' })
  const navigate = useNavigate()
  const canWrite = useCanWrite()
  const [editing, setEditing] = useState(false)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)

  const schema = useEntitySchema('organization')
  const { data: customer, isLoading, isError, error } = useOrganization(customerId)
  const update = useUpdateOrganization(customerId)
  const remove = useDeleteOrganization()
  const people = useOrganizationPeople(customerId)
  // The deals and the contracts of a customer are parts of two modules: with one switched
  // off the part is not drawn and its read is not made (REB-953), not left to answer 404.
  const modules = useModules()
  const dealsOn = modules.on('deals')
  const contractsOn = modules.on('contracts')
  const deals = useOrganizationDeals(customerId, dealsOn && !modules.loading)
  // Not a request of its own: the shell banner already holds `gmailKeys.health`, so
  // this reads the same cache entry. See `useGmailConfigured` for why an installation
  // without Google gets no Email tab at all rather than a permanently empty one.
  const gmailConfigured = useGmailConfigured()
  const owners = useOrganizationOwners()

  if (isLoading) return <LoadingState className="m-8" />
  // A failed fetch and a genuine 404 both leave `customer` undefined once loading
  // ends -- collapsing them into the same "Cliente non trovato." a bare `!customer`
  // check would show is exactly the bug `DataTable`/`QueryErrorBanner` and
  // `Timeline`'s own `isError` branch exist to rule out everywhere else in this
  // product: a 500, a 502 or a dropped connection would tell the user the record
  // does not exist, when the truth is "we could not ask". `toProblem(error).status`
  // is the transport-level signal that actually distinguishes the two (see that
  // function's own docstring: it is never ambiguous, unlike the response body
  // shape) -- a real 404 keeps this screen's own wording, anything else reuses the
  // same banner every other failed request in this app shows, rather than a fourth
  // way of saying "something went wrong".
  if (isError) {
    if (toProblem(error).status === 404) return <p className="p-8">Cliente non trovato.</p>
    return (
      <div className="p-8">
        <QueryErrorBanner error={error} />
      </div>
    )
  }
  if (!customer) return <p className="p-8">Cliente non trovato.</p>

  const custom = schema.data?.custom_fields ?? []

  function archive() {
    if (!customer) return
    // Soft delete or not, the UI itself offers no visible undo (restoring today
    // means calling `POST /api/organizations/{id}/restore` directly, not a button
    // here) -- a plain confirm is a cheap guard against a misclick, not a
    // client-side re-implementation of any server rule.
    const confirmed = window.confirm(
      `Archiviare ${customer.legal_name}? Il cliente non comparirà più negli elenchi, ma i dati restano e l'operazione è reversibile.`,
    )
    if (!confirmed) return

    remove.mutate(customerId, {
      onSuccess: () => {
        toast.success('Cliente archiviato')
        void navigate({ to: '/app/organizations' })
      },
      onError: (error) => {
        // A customer with active deals is refused with a 409 that names how many
        // (`Conflict("organization", "...", active_deals=n)` -- see OrganizationService.
        // soft_delete). `toProblem(error).detail` alone is the fixed sentence
        // ("il cliente ha deal attivi: archivia prima i deal"); `active_deals` is
        // the count riding along as a separate structured field, not part of that
        // sentence, so it has to be appended explicitly or "naming how many" is
        // silently lost even though the server said it.
        const failed = toProblem(error)
        const activeDeals = failed.active_deals
        toast.error(
          typeof activeDeals === 'number' ? `${failed.detail} (${activeDeals})` : failed.detail,
        )
      },
    })
  }

  return (
    <>
      <EntityDetailLayout
        icon={Building2}
        title={customer.legal_name}
        subtitle="Cliente"
        entityType="organization"
        entityId={customerId}
        documents={<DocumentsTab owner={{ customerId }} />}
        invoices={
          <InvoicesTab
            owner={{ customerId }}
            // The customer is not asked for again: this tab *is* the answer.
            actions={canWrite ? <NewProformaButton customerId={customerId} /> : undefined}
          />
        }
        contracts={contractsOn ? <ContractsTab customerId={customerId} /> : undefined}
        economics={<EconomicsTab customerId={customerId} />}
        tasks={<EntityTask link={{ kind: 'organization', id: customerId }} />}
        emails={
          gmailConfigured ? (
            <EmailTab
              entityType="organization"
              entityId={customerId}
              domainSource={`${customer.website ?? ''}|${customer.email ?? ''}`}
            />
          ) : undefined
        }
        actions={
          canWrite && (
            <>
              <Button
                variant="outline"
                onClick={() => {
                  setProblem(null)
                  setEditing(true)
                }}
              >
                <Pencil className="mr-2 size-4" />
                Modifica
              </Button>
              <Button variant="destructive" onClick={archive} disabled={remove.isPending}>
                <Trash2 className="mr-2 size-4" />
                Archivia
              </Button>
            </>
          )
        }
        overview={
          <div className="grid gap-6 lg:grid-cols-2">
            {customer.status && (
              <div className="lg:col-span-2">
                <Badge variant="secondary">{customer.status}</Badge>
              </div>
            )}

            <Card>
              <CardContent className="pt-6">
                <h2 className="mb-3 font-semibold">Dati fiscali</h2>
                <Row label="P.IVA" value={displayNative(customer.vat_number)} />
                <Row label="Codice fiscale" value={displayNative(customer.tax_code)} />
                <Row label="Codice SDI" value={displayNative(customer.sdi_code)} />
                <Row label="PEC" value={displayNative(customer.certified_email)} />
                <Row
                  label="Indirizzo"
                  value={
                    customer.address
                      ? `${customer.address}, ${customer.postal_code ?? ''} ${customer.city ?? ''} (${customer.province ?? ''})`
                      : EMPTY
                  }
                />
                <Row label="Nazione" value={displayNative(customer.country)} />
                <Row label="Termini di pagamento" value={paymentTerms(customer)} />
              </CardContent>
            </Card>

            <Card>
              <CardContent className="pt-6">
                <h2 className="mb-3 font-semibold">Contatti</h2>
                <ContactPointsList points={customer.contact_points} />
                <Row label="Sito web" value={displayNative(customer.website)} />
                <Row label="Responsabile" value={owners.nameOf(customer.owner_id) ?? EMPTY} />
              </CardContent>
            </Card>

            {custom.length > 0 && (
              <Card className="lg:col-span-2">
                <CardContent className="pt-6">
                  <h2 className="mb-3 font-semibold">Campi personalizzati</h2>
                  {custom.map((field) => (
                    <Row
                      key={field.key}
                      label={field.label}
                      value={renderFieldValue(field, customer.custom_fields[field.key])}
                    />
                  ))}
                </CardContent>
              </Card>
            )}

            <div className="lg:col-span-2">
              <NotesSection entityType="organization" entityId={customerId} />
            </div>
          </div>
        }
        links={
          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardContent className="pt-6">
                <h2 className="mb-3 font-semibold">Persone ({people.data?.items.length ?? 0})</h2>
                <LinkedList<RelatedPerson>
                  query={people}
                  emptyMessage="Nessuna persona collegata."
                  renderItem={(person) => (
                    <Row
                      key={person.id}
                      label={[person.first_name, person.last_name].filter(Boolean).join(' ')}
                      value={displayNative(person.email)}
                    />
                  )}
                />
              </CardContent>
            </Card>
            {dealsOn && (
              <Card>
                <CardContent className="pt-6">
                  <h2 className="mb-3 font-semibold">Deal ({deals.data?.items.length ?? 0})</h2>
                  <LinkedList<RelatedDeal>
                    query={deals}
                    emptyMessage="Nessun deal collegato."
                    renderItem={(deal) => (
                      <Row key={deal.id} label={deal.name} value={formatDealValue(deal)} />
                    )}
                  />
                </CardContent>
              </Card>
            )}
          </div>
        }
      />

      <OrganizationForm
        title="Modifica cliente"
        open={editing}
        onOpenChange={setEditing}
        customFields={custom}
        owners={owners.canChange ? owners.options : undefined}
        initial={organizationToFormValues(customer)}
        problem={problem}
        busy={update.isPending}
        onSubmit={(values) => {
          setProblem(null)
          update.mutate(values, {
            onSuccess: () => {
              setEditing(false)
              toast.success('Cliente aggiornato')
            },
            onError: (error) => setProblem(toProblem(error)),
          })
        }}
      />
    </>
  )
}
