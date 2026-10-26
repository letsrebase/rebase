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

function messages(count: number): string {
  return count === 1 ? '1 messaggio' : `${count} messaggi`
}

export function CustomerCorrespondents({ customerId }: { customerId: string }) {
  const mayDiscover = useCan('discover_gmail_correspondents')
  const mayAdd = useCan('create_person')
  // Which customer the press was for, not a bare flag: the page can show another customer
  // without remounting this, and the read must wait for a press about that one.
  const [askedFor, setAskedFor] = useState<string | null>(null)
  const asked = askedFor === customerId
  const found = useCustomerCorrespondents(customerId, asked)
  const adding = useAddCorrespondent(customerId)

  // A readonly person would get a refusal for a read that spends the owner's quota.
  if (!mayDiscover) return null

  function add(indirizzo: string, nome: string) {
    adding.mutate(
      { indirizzo, nome },
      {
        onSuccess: () => toast.success(`${nome || indirizzo} aggiunto alle persone`),
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
          <Button variant="outline" onClick={() => setAskedFor(customerId)}>
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
      ) : found.data.corrispondenti.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          Nessuno ha scritto o ricevuto posta da {found.data.dominio} nella casella collegata.
        </p>
      ) : (
        <ul
          aria-label={`Indirizzi di ${found.data.dominio} nella casella`}
          className="divide-y border"
        >
          {found.data.corrispondenti.map((person) => (
            <li key={person.indirizzo} className="flex flex-wrap items-center gap-3 p-3">
              <div className="min-w-0 flex-1">
                <p className="text-sm">
                  {person.nome ? <span className="font-medium">{person.nome} </span> : null}
                  <span className={person.nome ? 'text-muted-foreground' : 'font-medium'}>
                    {person.indirizzo}
                  </span>
                </p>
                <p className="text-muted-foreground text-xs">
                  {messages(person.messaggi)}
                  {person.ultimo_messaggio
                    ? `, l’ultimo il ${formatInstant(person.ultimo_messaggio)}`
                    : ''}
                </p>
              </div>
              {person.gia_in_anagrafica ? (
                <span className="text-muted-foreground text-sm">già in anagrafica</span>
              ) : mayAdd ? (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={adding.isPending}
                  aria-label={`Aggiungi come persona: ${person.nome || person.indirizzo}`}
                  onClick={() => add(person.indirizzo, person.nome)}
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
