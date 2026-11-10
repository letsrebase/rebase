import { X } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { SEARCH_DEBOUNCE_MS } from '@/features/search/queries'
import { useDebounced } from '@/hooks/useDebounced'
import { useOrganization, useOrganizations, type Organization } from './queries'

/** How many matches the list shows: enough to find a customer by its first letters, few
 *  enough that the list is read rather than scrolled. More is a longer term away. */
const MATCHES = 8

export type PickedOrganization = Pick<Organization, 'id' | 'legal_name'>

/** The role the matches are drawn from, and the words the list says it with. Customers when
 *  none is asked for, which is every caller but the purchase form's supplier. */
type PickerRole = 'customer' | 'supplier'
const ROLE_WORDS: Record<PickerRole, { list: string; none: string; more: string }> = {
  customer: {
    list: 'Clienti',
    none: 'Nessun cliente.',
    more: 'Ci sono altri clienti: scrivi per restringere l’elenco.',
  },
  supplier: {
    list: 'Fornitori',
    none: 'Nessun fornitore.',
    more: 'Ci sono altri fornitori: scrivi per restringere l’elenco.',
  },
}

/**
 * Picks one customer by asking the server for the ones that match what was typed.
 *
 * The one customer picker of the app. It replaces four dropdowns that each loaded
 * `limit: 200` organizations whole, and silently stopped at the 200th: a space has as many
 * organizations as it has, so «scegli dall'elenco» has to be a search, the same gesture as
 * `tasks/RecordPicker`'s. A chosen customer is shown by name, with a button to take it
 * off and search again.
 *
 * `value` is the customer's id and the name is read from it (`useOrganization`), so a form
 * that only stores the id -- a person's stored customer, say -- shows the name on open.
 * A customer picked here is named from the pick; a caller that already has the name
 * passes it as `selectedName`. Either way no read is made.
 * `legalEntityId` limits the matches to one legal entity's organizations; without it the sidebar's
 * legal entity applies, as on every list of organizations.
 */
export function OrganizationPicker({
  id,
  value,
  onChange,
  selectedName,
  legalEntityId,
  role,
  placeholder = 'Cerca un cliente per ragione sociale, P.IVA o email…',
  ariaLabel = 'Cerca un cliente',
  disabled = false,
}: {
  id: string
  value: string | null
  onChange: (customer: PickedOrganization | null) => void
  selectedName?: string
  legalEntityId?: string | null
  role?: PickerRole
  placeholder?: string
  ariaLabel?: string
  disabled?: boolean
}) {
  const [term, setTerm] = useState('')
  // The customer picked in this session: its name is known, so no read is made for it.
  const [picked, setPicked] = useState<PickedOrganization | null>(null)
  const typed = term.trim()
  const settled = useDebounced(typed, SEARCH_DEBOUNCE_MS)
  const chosen = value !== null && value !== ''
  const matches = useOrganizations(
    {
      search: settled || undefined,
      limit: MATCHES,
      legal_entity_id: legalEntityId ?? undefined,
      role,
    },
    // A chosen customer needs no list of matches.
    { enabled: !chosen },
  )
  const knownName = selectedName ?? (picked?.id === value ? picked.legal_name : undefined)
  const named = useOrganization(value ?? '', { enabled: chosen && knownName === undefined })

  if (chosen) {
    return (
      <div id={id} className="flex items-center justify-between gap-2 border px-3 py-2 text-sm">
        <span className="min-w-0 truncate">
          {knownName ?? named.data?.legal_name ?? 'Caricamento…'}
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
  const words = ROLE_WORDS[role ?? 'customer']
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
      <ul aria-label={words.list} className="max-h-44 divide-y overflow-y-auto border text-sm">
        {matches.isError ? (
          <li className="px-3 py-2 text-destructive">La ricerca non è disponibile.</li>
        ) : matches.data === undefined ? (
          <li className="px-3 py-2 text-muted-foreground">Ricerca in corso…</li>
        ) : items.length === 0 ? (
          <li className="px-3 py-2 text-muted-foreground">
            {settled === '' ? words.none : `Nessun risultato per «${settled}»`}
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
                <span className="truncate">{customer.legal_name}</span>
              </button>
            </li>
          ))
        )}
      </ul>
      {matches.data?.next_cursor != null && (
        <p className="text-xs text-muted-foreground">
          {words.more}
        </p>
      )}
    </div>
  )
}
