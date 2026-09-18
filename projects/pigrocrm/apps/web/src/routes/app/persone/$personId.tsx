import { createFileRoute, useNavigate, useParams } from '@tanstack/react-router'
import { Pencil, Trash2, Users } from 'lucide-react'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { renderFieldValue } from '@/components/DynamicFieldRenderer'
import { EntityDetailLayout } from '@/components/EntityDetailLayout'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Card, CardContent } from '@rebase/ui/card'
import { Skeleton } from '@rebase/ui/skeleton'
import { useCustomer } from '@/features/customers/queries'
import { EmailTab } from '@/features/gmail/EmailTab'
import { useGmailConfigured } from '@/features/gmail/queries'
import { displayNative } from '@/features/people/columns'
import { PersonForm, personToFormValues } from '@/features/people/PersonForm'
import { useDeletePerson, usePerson, useUpdatePerson } from '@/features/people/queries'
import { toProblem, type ProblemDetail } from '@/lib/api'
import { useCanWrite } from '@/lib/auth'
import { useEntitySchema } from '@/lib/schema'

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 border-b py-2 last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-right font-medium">{value}</span>
    </div>
  )
}

/**
 * Split into its own component so `useCustomer` is only ever mounted -- and
 * therefore only ever called -- with a real id. `person.customer_id` is `null`
 * for most people (`PersonService._check_customer`'s own docstring: a contact
 * may exist with no customer at all), and `useCustomer('')` is not a safe
 * stand-in for "don't fetch": `/api/customers/{customer_id}` with an empty path
 * segment does not match that route at all, and Starlette's default
 * trailing-slash redirect then lands the request on plain `/api/customers` --
 * the *list* endpoint -- which still resolves 200. Nothing would throw or log,
 * but every visit to a customer-less person would silently re-fetch the entire
 * customer list for a value that is then never even used (`CustomerPage` has no
 * `ragione_sociale` at its top level, so the card would just show '…' forever
 * regardless of the response). A conditionally *rendered* component, rather than
 * a conditionally *called* hook, keeps `useCustomer` itself untouched -- no
 * change needed in `features/customers/queries.ts` -- and only ever queries when
 * there is a real customer to look up. The parent only renders this at all
 * inside the `person.customer_id ? ... : ...` branch below.
 */
function LinkedCustomerRow({ customerId }: { customerId: string }) {
  const customer = useCustomer(customerId)
  return <Row label="Ragione sociale" value={customer.data?.ragione_sociale ?? '…'} />
}

export function PersonDetail() {
  const { personId } = useParams({ from: '/app/persone/$personId' })
  const navigate = useNavigate()
  const canWrite = useCanWrite()
  const [editing, setEditing] = useState(false)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)

  const schema = useEntitySchema('person')
  const { data: person, isLoading, isError, error } = usePerson(personId)
  const update = useUpdatePerson(personId)
  const remove = useDeletePerson()
  // Shares the shell banner's cached health row -- see `useGmailConfigured`.
  const gmailConfigured = useGmailConfigured()

  if (isLoading) return <Skeleton className="m-8 h-96" />
  // See `routes/app/clienti/$customerId.tsx`'s identical guard for the full
  // reasoning: without this, a 500/502/dropped connection reads as "Persona non
  // trovata." exactly like a genuine 404 does, since both leave `person`
  // undefined once loading ends. `toProblem(error).status` is the one place that
  // is never ambiguous; only a real 404 keeps this screen's own wording.
  if (isError) {
    if (toProblem(error).status === 404) return <p className="p-8">Persona non trovata.</p>
    return (
      <div className="p-8">
        <QueryErrorBanner error={error} />
      </div>
    )
  }
  if (!person) return <p className="p-8">Persona non trovata.</p>

  const custom = schema.data?.custom_fields ?? []
  const fullName = `${person.nome} ${person.cognome ?? ''}`.trim()

  function archive() {
    if (!person) return
    // No visible undo in the UI (restoring today means calling
    // `POST /api/people/{id}/restore` directly, not a button here) -- a plain
    // confirm is a cheap guard against a misclick, not a client-side
    // re-implementation of any server rule. Unlike `CustomerService.soft_delete`,
    // `PersonService.soft_delete` never refuses (no entity in this slice holds a
    // foreign key to a person), so there is no conflict count to fold into this
    // copy the way `CustomerDetail`'s own `archive` does.
    const confirmed = window.confirm(
      `Archiviare ${fullName}? La persona non comparirà più negli elenchi, ma i dati restano e l'operazione è reversibile.`,
    )
    if (!confirmed) return

    remove.mutate(person.id, {
      onSuccess: () => {
        toast.success('Persona archiviata')
        void navigate({ to: '/app/persone' })
      },
      onError: (error) => toast.error(toProblem(error).detail),
    })
  }

  return (
    <>
      <EntityDetailLayout
        icon={Users}
        title={fullName}
        subtitle={person.ruolo ?? 'Persona'}
        entityType="person"
        entityId={personId}
        emails={gmailConfigured ? <EmailTab entityType="person" entityId={personId} /> : undefined}
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
            <Card>
              <CardContent className="pt-6">
                <h2 className="mb-3 font-semibold">Contatti</h2>
                <Row label="Email" value={displayNative(person.email)} />
                <Row label="Telefono" value={displayNative(person.telefono)} />
                <Row label="LinkedIn" value={displayNative(person.linkedin)} />
              </CardContent>
            </Card>

            {custom.length > 0 && (
              <Card>
                <CardContent className="pt-6">
                  <h2 className="mb-3 font-semibold">Campi personalizzati</h2>
                  {custom.map((field) => (
                    <Row
                      key={field.key}
                      label={field.label}
                      value={renderFieldValue(field, person.custom_fields[field.key])}
                    />
                  ))}
                </CardContent>
              </Card>
            )}

            {person.note && (
              <Card className="lg:col-span-2">
                <CardContent className="pt-6">
                  <h2 className="mb-3 font-semibold">Note</h2>
                  <p className="whitespace-pre-wrap text-sm">{person.note}</p>
                </CardContent>
              </Card>
            )}
          </div>
        }
        links={
          <Card>
            <CardContent className="pt-6">
              <h2 className="mb-3 font-semibold">Cliente</h2>
              {person.customer_id ? (
                <LinkedCustomerRow customerId={person.customer_id} />
              ) : (
                <p className="text-muted-foreground">
                  Questa persona non è associata a un cliente.
                </p>
              )}
            </CardContent>
          </Card>
        }
      />

      <PersonForm
        title="Modifica persona"
        open={editing}
        onOpenChange={setEditing}
        customFields={custom}
        initial={personToFormValues(person)}
        problem={problem}
        busy={update.isPending}
        onSubmit={(values) => {
          setProblem(null)
          update.mutate(values, {
            onSuccess: () => {
              setEditing(false)
              toast.success('Persona aggiornata')
            },
            onError: (error) => setProblem(toProblem(error)),
          })
        }}
      />
    </>
  )
}

export const Route = createFileRoute('/app/persone/$personId')({ component: PersonDetail })
