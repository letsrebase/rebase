/**
 * The organizations the connected mailbox proposes, as a list to tick (spec 2026-09-16 §5,
 * REB-223). Under the Home's Gmail door once a mailbox is connected, and in Clienti as
 * «Proponi dalla casella», for whoever connects Gmail after the space has work in it.
 *
 * Each proposal is a domain the owner wrote to in the last twelve months, with the
 * conversations, the last message and the people seen there. Nothing is ticked at
 * first: a supplier or a friend's company is on the same list, and only the person knows
 * which are organizations. Ticking one opens its company name for correction and ticks its
 * people, except those already in the address book, which the import leaves alone.
 * `POST /api/organizations/from-suggestions` creates the ticked ones together or not at all.
 */
import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { toast } from '@rebase/ui/sonner'
import { toProblem } from '@/lib/api'
import { formatInstant } from './instants'
import { useOrganizationSuggestions, useImportSuggestions, type SuggestedOrganization } from './suggestions'

/** What the person decided about one ticked proposal. */
interface Choice {
  name: string
  people: Record<string, boolean>
}

function firstChoice(proposal: SuggestedOrganization): Choice {
  return {
    name: proposal.name,
    people: Object.fromEntries(
      proposal.people.filter((person) => !person.already_in_directory).map((person) => [person.address, true]),
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

export function SuggestedOrganizations({ onImported }: { onImported?: (count: number) => void }) {
  const suggestions = useOrganizationSuggestions()
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

  const picked = proposals.filter((proposal) => chosen[proposal.domain] !== undefined)
  const unnamed = picked.some((proposal) => (chosen[proposal.domain]?.name ?? '').trim() === '')

  function toggle(proposal: SuggestedOrganization) {
    setChosen((current) => {
      const next = { ...current }
      if (next[proposal.domain]) delete next[proposal.domain]
      else next[proposal.domain] = firstChoice(proposal)
      return next
    })
  }

  function update(domain: string, change: (choice: Choice) => Choice) {
    setChosen((current) => {
      const choice = current[domain]
      return choice ? { ...current, [domain]: change(choice) } : current
    })
  }

  function submit() {
    importing.mutate(
      {
        organizations: picked.map((proposal) => {
          const choice = chosen[proposal.domain] ?? firstChoice(proposal)
          return {
            domain: proposal.domain,
            legal_name: choice.name.trim(),
            people: proposal.people
              .filter((person) => choice.people[person.address])
              .map((person) => ({ address: person.address, name: person.name })),
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
          const choice = chosen[proposal.domain]
          const id = `proposta-${proposal.domain}`
          return (
            <li key={proposal.domain} className="space-y-2 p-3">
              <div className="flex items-start gap-3">
                <Checkbox id={id} checked={choice !== undefined} onCheckedChange={() => toggle(proposal)} />
                <div className="min-w-0 flex-1 space-y-1">
                  <Label htmlFor={id} className="flex flex-wrap items-baseline gap-x-2">
                    <span className="font-medium">{proposal.name}</span>
                    <span className="text-muted-foreground text-xs">{proposal.domain}</span>
                  </Label>
                  <p className="text-muted-foreground text-xs">
                    {conversations(proposal.conversations)}
                    {proposal.last_message ? `, l’ultima il ${formatInstant(proposal.last_message)}` : ''}
                  </p>
                  {choice ? (
                    <Input
                      aria-label={`Nome del cliente per ${proposal.domain}`}
                      value={choice.name}
                      onChange={(event) => {
                        const name = event.target.value
                        update(proposal.domain, (current) => ({ ...current, name }))
                      }}
                    />
                  ) : null}
                  <ul aria-label={`Persone di ${proposal.domain}`} className="space-y-1 text-sm">
                    {proposal.people.map((person) => {
                      const personId = `${id}-${person.address}`
                      const label = person.name ? `${person.name} <${person.address}>` : person.address
                      return (
                        <li key={person.address} className="flex items-center gap-2">
                          {choice && !person.already_in_directory ? (
                            <Checkbox
                              id={personId}
                              checked={choice.people[person.address] === true}
                              onCheckedChange={(checked) =>
                                update(proposal.domain, (current) => ({
                                  ...current,
                                  people: { ...current.people, [person.address]: checked === true },
                                }))
                              }
                            />
                          ) : null}
                          <Label htmlFor={personId} className="font-normal">
                            {label}
                            {person.already_in_directory ? (
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
