import { X } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { SEARCH_DEBOUNCE_MS } from '@/features/search/queries'
import { useDebounced } from '@/hooks/useDebounced'
import { useCustomer, useCustomers, type Customer } from './queries'

/** How many matches the list shows: enough to find a customer by its first letters, few
 *  enough that the list is read rather than scrolled. More is a longer term away. */
const MATCHES = 8

export type PickedCustomer = Pick<Customer, 'id' | 'ragione_sociale'>

/**
 * Picks one customer by asking the server for the ones that match what was typed.
 *
 * The one customer picker of the app. It replaces four dropdowns that each loaded
 * `limit: 200` customers whole, and silently stopped at the 200th: a space has as many
 * customers as it has, so «scegli dall'elenco» has to be a search, the same gesture as
 * `attivita/RecordPicker`'s. A chosen customer is shown by name, with a button to take it
 * off and search again.
 *
 * `value` is the customer's id and the name is read from it (`useCustomer`), so a form
 * that only stores the id -- a person's stored customer, say -- shows the name on open.
 * A customer picked here is named from the pick; a caller that already has the name
 * passes it as `selectedName`. Either way no read is made.
 * `aziendaId` limits the matches to one azienda's customers; without it the sidebar's
 * azienda applies, as on every list of customers.
 */
export function CustomerPicker({
  id,
  value,
  onChange,
  selectedName,
  aziendaId,
  placeholder = 'Cerca un cliente per ragione sociale, P.IVA o email…',
  ariaLabel = 'Cerca un cliente',
  disabled = false,
}: {
  id: string
  value: string | null
  onChange: (customer: PickedCustomer | null) => void
  selectedName?: string
  aziendaId?: string | null
  placeholder?: string
  ariaLabel?: string
  disabled?: boolean
}) {
  const [term, setTerm] = useState('')
  // The customer picked in this session: its name is known, so no read is made for it.
  const [picked, setPicked] = useState<PickedCustomer | null>(null)
  const typed = term.trim()
  const settled = useDebounced(typed, SEARCH_DEBOUNCE_MS)
  const chosen = value !== null && value !== ''
  const matches = useCustomers(
    { search: settled || undefined, limit: MATCHES, azienda_id: aziendaId ?? undefined },
    // A chosen customer needs no list of matches.
    { enabled: !chosen },
  )
  const knownName = selectedName ?? (picked?.id === value ? picked.ragione_sociale : undefined)
  const named = useCustomer(value ?? '', { enabled: chosen && knownName === undefined })

  if (chosen) {
    return (
      <div id={id} className="flex items-center justify-between gap-2 border px-3 py-2 text-sm">
        <span className="min-w-0 truncate">
          {knownName ?? named.data?.ragione_sociale ?? 'Caricamento…'}
        </span>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label="Rimuovi il cliente"
          disabled={disabled}
          onClick={() => {
            setTerm('')
            onChange(null)
          }}
        >
          <X className="size-4" />
        </Button>
      </div>
    )
  }

  const items = matches.data?.items ?? []
  return (
    <div className="space-y-2">
      <Input
        id={id}
        aria-label={ariaLabel}
        value={term}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(event) => setTerm(event.target.value)}
      />
      <ul aria-label="Clienti" className="max-h-44 divide-y overflow-y-auto border text-sm">
        {matches.isError ? (
          <li className="px-3 py-2 text-destructive">La ricerca non è disponibile.</li>
        ) : matches.data === undefined ? (
          <li className="px-3 py-2 text-muted-foreground">Ricerca in corso…</li>
        ) : items.length === 0 ? (
          <li className="px-3 py-2 text-muted-foreground">
            {settled === '' ? 'Nessun cliente.' : `Nessun risultato per «${settled}»`}
          </li>
        ) : (
          items.map((customer) => (
            <li key={customer.id}>
              <button
                type="button"
                disabled={disabled}
                className="flex w-full items-center px-3 py-2 text-left hover:bg-muted focus-visible:bg-muted focus-visible:outline-none"
                onClick={() => {
                  setTerm('')
                  setPicked(customer)
                  onChange(customer)
                }}
              >
                <span className="truncate">{customer.ragione_sociale}</span>
              </button>
            </li>
          ))
        )}
      </ul>
      {matches.data?.next_cursor != null && (
        <p className="text-xs text-muted-foreground">
          Ci sono altri clienti: scrivi per restringere l’elenco.
        </p>
      )}
    </div>
  )
}
