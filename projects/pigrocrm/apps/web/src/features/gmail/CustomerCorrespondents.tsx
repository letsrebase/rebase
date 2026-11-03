/**
 * Who wrote from this customer's domain, on its Email tab (REB-766). One press asks the
 * connected mailbox which addresses at the customer's domain it has exchanged mail with
 * (`GET /api/gmail/customers/{id}/correspondents`); each one that is not yet in the
 * address book is added to the customer as a person with one press, named from the mail
 * header the way the Clienti proposals name theirs. Nothing is written until that press.
 *
 * Behind a press and not read on open: the answer costs a read of every conversation
 * with the domain from the owner's own Gmail quota.
 */
import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { toast } from '@rebase/ui/sonner'
import { toProblem } from '@/lib/api'
import { useCan } from '@/lib/auth'
import { formatInstant } from './instants'
import { useAddCorrespondent, useCustomerCorrespondents } from './correspondents'

function conversations(count: number): string {
  return count === 1 ? '1 conversazione' : `${count} conversazioni`
}

export function CustomerCorrespondents({
  customerId,
  domainSource,
}: {
  customerId: string
  domainSource: string
}) {
  const mayDiscover = useCan('discover_gmail_correspondents')
  const mayAdd = useCan('create_person')
  // What the press was for, not a bare flag: the page can show another customer without
  // remounting this, and a saved website or email changes the domain asked about. Either
  // way the read waits for a new press.
  const pressed = `${customerId}|${domainSource}`
  const [askedFor, setAskedFor] = useState<string | null>(null)
  const asked = askedFor === pressed
  const found = useCustomerCorrespondents(customerId, asked)
  const adding = useAddCorrespondent(customerId)

  // A readonly person would get a refusal for a read that spends the owner's quota.
  if (!mayDiscover) return null

  function add(address: string, name: string) {
    adding.mutate(
      { address, name },
      {
        onSuccess: () => toast.success(`${name || address} aggiunto alle persone`),
        onError: (error) => toast.error(toProblem(error).detail),
      },
    )
  }

  return (
    <section className="space-y-3">
      <h3 className="text-sm font-medium text-muted-foreground">Chi ha scritto</h3>
      {!asked ? (
        <div className="space-y-2">
          <p className="text-sm">
            Cerco nella casella collegata gli indirizzi del dominio di questo cliente con cui
            hai scambiato posta, per aggiungerli come persone.
          </p>
          <Button variant="outline" onClick={() => setAskedFor(pressed)}>
            Cerca chi ha scritto dal dominio del cliente
          </Button>
        </div>
      ) : found.isPending ? (
        <p role="status" className="text-muted-foreground text-sm">
          Leggo le conversazioni con il dominio del cliente…
        </p>
      ) : found.isError ? (
        <div className="space-y-2">
          <p role="alert" className="text-sm">
            {toProblem(found.error).detail}
          </p>
          <Button variant="outline" size="sm" onClick={() => void found.refetch()}>
            Riprova
          </Button>
        </div>
      ) : found.data.matching.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          Nessuno ha scritto o ricevuto posta da {found.data.domain} nella casella collegata.
        </p>
      ) : (
        <ul
          aria-label={`Indirizzi di ${found.data.domain} nella casella`}
          className="divide-y border"
        >
          {found.data.matching.map((person) => (
            <li key={person.address} className="flex flex-wrap items-center gap-3 p-3">
              <div className="min-w-0 flex-1">
                <p className="text-sm">
                  {person.name ? <span className="font-medium">{person.name} </span> : null}
                  <span className={person.name ? 'text-muted-foreground' : 'font-medium'}>
                    {person.address}
                  </span>
                </p>
                <p className="text-muted-foreground text-xs">
                  {conversations(person.conversations)}
                  {person.last_message
                    ? `, l’ultimo messaggio il ${formatInstant(person.last_message)}`
                    : ''}
                </p>
              </div>
              {person.already_in_directory ? (
                <span className="text-muted-foreground text-sm">già in anagrafica</span>
              ) : mayAdd ? (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={adding.isPending}
                  aria-label={`Aggiungi come persona: ${person.name || person.address}`}
                  onClick={() => add(person.address, person.name)}
                >
                  Aggiungi come persona
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
