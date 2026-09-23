/**
 * The customers the connected mailbox proposes, as a list to tick (spec 2026-09-16 §5,
 * REB-223). Under the Home's Gmail door once a mailbox is connected, and in Clienti as
 * «Proponi dalla casella», for whoever connects Gmail after the space has work in it.
 *
 * Each proposal is a domain the owner wrote to in the last twelve months, with the
 * conversations, the last message and the people seen there. Nothing is ticked at
 * first: a supplier or a friend's company is on the same list, and only the person knows
 * which are customers. Ticking one opens its company name for correction and ticks its
 * people, except those already in the address book, which the import leaves alone.
 * `POST /api/customers/from-suggestions` creates the ticked ones together or not at all.
 */
import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { toast } from '@rebase/ui/sonner'
import { toProblem } from '@/lib/api'
import { formatInstant } from './instants'
import { useCustomerSuggestions, useImportSuggestions, type SuggestedCustomer } from './suggestions'

/** What the person decided about one ticked proposal. */
interface Choice {
  nome: string
  persone: Record<string, boolean>
}

function firstChoice(proposal: SuggestedCustomer): Choice {
  return {
    nome: proposal.nome,
    persone: Object.fromEntries(
      proposal.persone.filter((person) => !person.gia_in_anagrafica).map((person) => [person.indirizzo, true]),
    ),
  }
}

function conversations(count: number): string {
  return count === 1 ? '1 conversazione' : `${count} conversazioni`
}

function importLabel(count: number): string {
  if (count === 0) return 'Importa i clienti spuntati'
  return count === 1 ? 'Importa 1 cliente' : `Importa ${count} clienti`
}

export function SuggestedCustomers({ onImported }: { onImported?: (count: number) => void }) {
  const suggestions = useCustomerSuggestions()
  const importing = useImportSuggestions()
  const [chosen, setChosen] = useState<Record<string, Choice>>({})

  if (suggestions.isPending) {
    return (
      <p role="status" className="text-muted-foreground text-sm">
        Leggo la posta che hai inviato negli ultimi dodici mesi…
      </p>
    )
  }
  if (suggestions.isError) {
    return (
      <p role="alert" className="text-sm">
        {toProblem(suggestions.error).detail}
      </p>
    )
  }
  const proposals = suggestions.data
  if (proposals.length === 0) {
    return (
      <p className="text-muted-foreground text-sm">
        Nessuna azienda da proporre: nella posta che hai inviato negli ultimi dodici mesi non
        c’è nessuno che non sia già un cliente.
      </p>
    )
  }

  const picked = proposals.filter((proposal) => chosen[proposal.dominio] !== undefined)
  const unnamed = picked.some((proposal) => (chosen[proposal.dominio]?.nome ?? '').trim() === '')

  function toggle(proposal: SuggestedCustomer) {
    setChosen((current) => {
      const next = { ...current }
      if (next[proposal.dominio]) delete next[proposal.dominio]
      else next[proposal.dominio] = firstChoice(proposal)
      return next
    })
  }

  function update(dominio: string, change: (choice: Choice) => Choice) {
    setChosen((current) => {
      const choice = current[dominio]
      return choice ? { ...current, [dominio]: change(choice) } : current
    })
  }

  function submit() {
    importing.mutate(
      {
        clienti: picked.map((proposal) => {
          const choice = chosen[proposal.dominio] ?? firstChoice(proposal)
          return {
            dominio: proposal.dominio,
            ragione_sociale: choice.nome.trim(),
            persone: proposal.persone
              .filter((person) => choice.persone[person.indirizzo])
              .map((person) => ({ indirizzo: person.indirizzo, nome: person.nome })),
          }
        }),
      },
      {
        onSuccess: (created) => {
          toast.success(created.length === 1 ? '1 cliente importato' : `${created.length} clienti importati`)
          setChosen({})
          onImported?.(created.length)
        },
        onError: (error) => toast.error(toProblem(error).detail),
      },
    )
  }

  return (
    <div className="space-y-3">
      <p className="text-sm">
        Le aziende a cui hai scritto negli ultimi dodici mesi. Spunta quelle che sono clienti:
        le creo con le persone che hai sentito.
      </p>
      <ul aria-label="Clienti proposti dalla casella" className="divide-y border">
        {proposals.map((proposal) => {
          const choice = chosen[proposal.dominio]
          const id = `proposta-${proposal.dominio}`
          return (
            <li key={proposal.dominio} className="space-y-2 p-3">
              <div className="flex items-start gap-3">
                <Checkbox id={id} checked={choice !== undefined} onCheckedChange={() => toggle(proposal)} />
                <div className="min-w-0 flex-1 space-y-1">
                  <Label htmlFor={id} className="flex flex-wrap items-baseline gap-x-2">
                    <span className="font-medium">{proposal.nome}</span>
                    <span className="text-muted-foreground text-xs">{proposal.dominio}</span>
                  </Label>
                  <p className="text-muted-foreground text-xs">
                    {conversations(proposal.conversazioni)}
                    {proposal.ultimo_messaggio ? `, l’ultima il ${formatInstant(proposal.ultimo_messaggio)}` : ''}
                  </p>
                  {choice ? (
                    <Input
                      aria-label={`Nome del cliente per ${proposal.dominio}`}
                      value={choice.nome}
                      onChange={(event) => {
                        const nome = event.target.value
                        update(proposal.dominio, (current) => ({ ...current, nome }))
                      }}
                    />
                  ) : null}
                  <ul aria-label={`Persone di ${proposal.dominio}`} className="space-y-1 text-sm">
                    {proposal.persone.map((person) => {
                      const personId = `${id}-${person.indirizzo}`
                      const label = person.nome ? `${person.nome} <${person.indirizzo}>` : person.indirizzo
                      return (
                        <li key={person.indirizzo} className="flex items-center gap-2">
                          {choice && !person.gia_in_anagrafica ? (
                            <Checkbox
                              id={personId}
                              checked={choice.persone[person.indirizzo] === true}
                              onCheckedChange={(checked) =>
                                update(proposal.dominio, (current) => ({
                                  ...current,
                                  persone: { ...current.persone, [person.indirizzo]: checked === true },
                                }))
                              }
                            />
                          ) : null}
                          <Label htmlFor={personId} className="font-normal">
                            {label}
                            {person.gia_in_anagrafica ? (
                              <span className="text-muted-foreground"> · già in anagrafica</span>
                            ) : null}
                          </Label>
                        </li>
                      )
                    })}
                  </ul>
                </div>
              </div>
            </li>
          )
        })}
      </ul>
      <Button disabled={picked.length === 0 || unnamed || importing.isPending} onClick={submit}>
        {importing.isPending ? 'Importo…' : importLabel(picked.length)}
      </Button>
    </div>
  )
}
