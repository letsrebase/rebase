import { createFileRoute, useNavigate, useParams } from '@tanstack/react-router'
import { Building2, Pencil, Trash2 } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { toast } from '@rebase/ui/sonner'
import { renderFieldValue } from '@/components/DynamicFieldRenderer'
import { EntityDetailLayout } from '@/components/EntityDetailLayout'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Card, CardContent } from '@rebase/ui/card'
import { Skeleton } from '@rebase/ui/skeleton'
import { EconomicsTab } from '@/features/analytics/EconomicsTab'
import { CustomerForm, customerToFormValues } from '@/features/customers/CustomerForm'
import { displayNative } from '@/features/customers/columns'
import {
  useCustomer,
  useCustomerDeals,
  useCustomerPeople,
  useDeleteCustomer,
  useUpdateCustomer,
  type RelatedDeal,
  type RelatedPerson,
} from '@/features/customers/queries'
import { DocumentsTab } from '@/features/documents/DocumentsTab'
import { EmailTab } from '@/features/gmail/EmailTab'
import { useGmailConfigured } from '@/features/gmail/queries'
import { InvoicesTab } from '@/features/invoices/InvoicesTab'
import { NewProformaButton } from '@/features/invoices/NewProformaDialog'
import { toProblem, type ProblemDetail } from '@/lib/api'
import { useCanWrite } from '@/lib/auth'
import { useEntitySchema } from '@/lib/schema'

const EMPTY = '—'

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 border-b py-2 last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-right font-medium">{value}</span>
    </div>
  )
}

const currencyFormatter = new Intl.NumberFormat('it-IT', {
  style: 'currency',
  currency: 'EUR',
  // See DynamicFieldRenderer.tsx's `renderFieldValue` for why `'always'` and not the
  // ICU default: it-IT's "auto" grouping withholds the thousands separator below
  // five integer digits, and every Italian reader expects one starting at four.
  useGrouping: 'always',
})

function formatCurrency(value: string | null): string {
  return value === null ? EMPTY : currencyFormatter.format(Number(value))
}

/** A short preview list, not a paginated table: this tab exists so a customer's
 *  linked people/deals are visible at all here, not to replace the full Persone/
 *  Deal screens (`routes/app/persone`, `routes/app/deal`). Loading, error and
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
    return (
      <div className="space-y-2">
        <Skeleton className="h-6 w-full" />
        <Skeleton className="h-6 w-full" />
      </div>
    )
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

export function CustomerDetail() {
  const { customerId } = useParams({ from: '/app/clienti/$customerId' })
  const navigate = useNavigate()
  const canWrite = useCanWrite()
  const [editing, setEditing] = useState(false)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)

  const schema = useEntitySchema('customer')
  const { data: customer, isLoading, isError, error } = useCustomer(customerId)
  const update = useUpdateCustomer(customerId)
  const remove = useDeleteCustomer()
  const people = useCustomerPeople(customerId)
  const deals = useCustomerDeals(customerId)
  // Not a request of its own: the shell banner already holds `gmailKeys.health`, so
  // this reads the same cache entry. See `useGmailConfigured` for why an installation
  // without Google gets no Email tab at all rather than a permanently empty one.
  const gmailConfigured = useGmailConfigured()

  if (isLoading) return <Skeleton className="m-8 h-96" />
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
    // means calling `POST /api/customers/{id}/restore` directly, not a button
    // here) -- a plain confirm is a cheap guard against a misclick, not a
    // client-side re-implementation of any server rule.
    const confirmed = window.confirm(
      `Archiviare ${customer.ragione_sociale}? Il cliente non comparirà più negli elenchi, ma i dati restano e l'operazione è reversibile.`,
    )
    if (!confirmed) return

    remove.mutate(customerId, {
      onSuccess: () => {
        toast.success('Cliente archiviato')
        void navigate({ to: '/app/clienti' })
      },
      onError: (error) => {
        // A customer with active deals is refused with a 409 that names how many
        // (`Conflict("customer", "...", active_deals=n)` -- see CustomerService.
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
        title={customer.ragione_sociale}
        subtitle="Cliente"
        entityType="customer"
        entityId={customerId}
        documents={<DocumentsTab owner={{ customerId }} />}
        invoices={
          <InvoicesTab
            owner={{ customerId }}
            // The customer is not asked for again: this tab *is* the answer.
            actions={canWrite ? <NewProformaButton customerId={customerId} /> : undefined}
          />
        }
        economics={<EconomicsTab customerId={customerId} />}
        emails={gmailConfigured ? <EmailTab entityType="customer" entityId={customerId} /> : undefined}
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
            {customer.stato && (
              <div className="lg:col-span-2">
                <Badge variant="secondary">{customer.stato}</Badge>
              </div>
            )}

            <Card>
              <CardContent className="pt-6">
                <h2 className="mb-3 font-semibold">Dati fiscali</h2>
                <Row label="P.IVA" value={displayNative(customer.partita_iva)} />
                <Row label="Codice fiscale" value={displayNative(customer.codice_fiscale)} />
                <Row label="Codice SDI" value={displayNative(customer.codice_sdi)} />
                <Row label="PEC" value={displayNative(customer.pec)} />
                <Row
                  label="Indirizzo"
                  value={
                    customer.indirizzo
                      ? `${customer.indirizzo}, ${customer.cap ?? ''} ${customer.comune ?? ''} (${customer.provincia ?? ''})`
                      : EMPTY
                  }
                />
                <Row label="Nazione" value={displayNative(customer.nazione)} />
              </CardContent>
            </Card>

            <Card>
              <CardContent className="pt-6">
                <h2 className="mb-3 font-semibold">Contatti</h2>
                <Row label="Email" value={displayNative(customer.email)} />
                <Row label="Telefono" value={displayNative(customer.telefono)} />
                <Row label="Sito web" value={displayNative(customer.sito_web)} />
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

            {customer.note && (
              <Card className="lg:col-span-2">
                <CardContent className="pt-6">
                  <h2 className="mb-3 font-semibold">Note</h2>
                  <p className="whitespace-pre-wrap text-sm">{customer.note}</p>
                </CardContent>
              </Card>
            )}
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
                      label={[person.nome, person.cognome].filter(Boolean).join(' ')}
                      value={displayNative(person.email)}
                    />
                  )}
                />
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-6">
                <h2 className="mb-3 font-semibold">Deal ({deals.data?.items.length ?? 0})</h2>
                <LinkedList<RelatedDeal>
                  query={deals}
                  emptyMessage="Nessun deal collegato."
                  renderItem={(deal) => (
                    <Row key={deal.id} label={deal.nome} value={formatCurrency(deal.valore_previsto)} />
                  )}
                />
              </CardContent>
            </Card>
          </div>
        }
      />

      <CustomerForm
        title="Modifica cliente"
        open={editing}
        onOpenChange={setEditing}
        customFields={custom}
        initial={customerToFormValues(customer)}
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

export const Route = createFileRoute('/app/clienti/$customerId')({ component: CustomerDetail })
