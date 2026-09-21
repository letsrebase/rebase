import { useNavigate } from '@tanstack/react-router'
import { Fragment, useEffect, useState } from 'react'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import {
  Command,
  CommandDialog,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from '@/components/ui/command'
import {
  ENTITY_LABELS,
  MIN_TERM_LENGTH,
  useGlobalSearch,
  type SearchEntity,
  type SearchGroup,
  type SearchHit,
} from './queries'

/**
 * The four renderings of §8.6 and §8.3, as four branches.
 *
 * They are branches and not captions on one list because a list rendered empty after a
 * failure *is* a wrong answer: it says "there is none" when the truth is "I do not know".
 * The error branch renders no `<CommandList>` at all, which is what makes the assertion
 * "the string «Nessun risultato» is absent from the DOM" hold structurally rather than by
 * a conditional someone can later invert. The error branch is also tested *before* the
 * loading one: on a failure `isPending` is false while `data` is still undefined, so a
 * loading-first guard answers a failed search with a spinner that never resolves.
 *
 * `cmdk` rather than `dialog` + `input`: an accessible combobox -- ARIA roles,
 * `aria-activedescendant`, keyboard navigation, results announced to a screen reader -- is
 * one of the things that is written badly by hand, and the cost is one small dependency in
 * a project that already carries `radix-ui`.
 *
 * `shouldFilter={false}` is load-bearing: `cmdk` filters and reorders client-side by
 * default, which would apply a second, different ranking on top of §8.5's. The server
 * ranks; this renders.
 */
export function CommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [term, setTerm] = useState('')
  const navigate = useNavigate()
  const search = useGlobalSearch(term)

  // Cmd/Ctrl+K from anywhere. Either modifier is accepted regardless of platform: a user
  // on a Mac keyboard plugged into Linux should not have to know which one this build
  // decided to draw in the header.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key.toLowerCase() === 'k' && (event.metaKey || event.ctrlKey)) {
        event.preventDefault()
        onOpenChange(!open)
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open, onOpenChange])

  function close() {
    onOpenChange(false)
    setTerm('')
  }

  /**
   * Every destination is a literal route id with its own params, never a hand-built
   * path string: `navigate({ to })` is typed against the generated route tree, so a
   * route that is renamed or removed breaks `tsc` here instead of 404-ing a user. The
   * switch is exhaustive, so a sixth searchable entity cannot be added to the API
   * without this file being told about it.
   */
  function openHit(hit: SearchHit) {
    close()
    switch (hit.entity) {
      case 'customer':
        return void navigate({ to: '/app/customers/$customerId', params: { customerId: hit.id } })
      case 'person':
        return void navigate({ to: '/app/people/$personId', params: { personId: hit.id } })
      case 'deal':
        return void navigate({ to: '/app/deal/$dealId', params: { dealId: hit.id } })
      case 'document':
        return void navigate({ to: '/app/documents/$documentId', params: { documentId: hit.id } })
      case 'invoice':
        return void navigate({ to: '/app/invoices/$invoiceId', params: { invoiceId: hit.id } })
    }
  }

  /**
   * Where a group's «vedi tutti» lands, or `null` where this app has no list view that
   * can honour the term. Offering to show "all 42" and then landing on an unfiltered
   * page -- or on no page at all -- is the same silent wrong answer §8.6 exists to
   * prevent, so the row is simply not rendered for those classes; their real count is
   * still stated in the heading.
   */
  function seeAll(entity: SearchEntity, term: string): (() => void) | null {
    switch (entity) {
      case 'customer':
        return () => void navigate({ to: '/app/customers', search: { search: term } })
      case 'person':
        return () => void navigate({ to: '/app/people', search: { search: term } })
      case 'deal':
        return () => void navigate({ to: '/app/deal/list', search: { search: term } })
      // Documents are reached from the customer or deal that owns them; the app has no
      // documents list route to filter.
      case 'document':
        return null
      // The invoice branch exists since Task C12, so this group does get results -- a row
      // opens the invoice. There is still no «vedi tutti» for it: `/app/invoices` has no
      // term filter to honour, and a link that dropped the term would answer a different
      // question from the one the palette was asked.
      case 'invoice':
        return null
    }
  }

  const tooShort = search.debounced.length < MIN_TERM_LENGTH
  const groups: SearchGroup[] = (search.data?.gruppi ?? []).filter(
    (group) => group.hits.length > 0,
  )

  /** "5 di 500" and "5 di 5" are different answers; «oltre» is how the ceiling is said. */
  function count(group: SearchGroup): string {
    return group.totale_e_un_minimo ? `oltre ${group.totale}` : String(group.totale)
  }

  return (
    <CommandDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Ricerca globale"
      description="Cerca clienti, persone, deal e documenti"
    >
      <Command shouldFilter={false}>
        <CommandInput
          value={term}
          onValueChange={setTerm}
          placeholder="Cerca clienti, persone, deal, documenti…"
          aria-label="Cerca in tutto il CRM"
        />

        {/* State 4 (not an error): an invitation, and no request was made. */}
        {tooShort && (
          <p className="px-4 py-6 text-sm text-muted-foreground">
            Continua a scrivere: servono almeno {MIN_TERM_LENGTH} caratteri.
          </p>
        )}

        {/* State 3: unavailable. No list is rendered at all. */}
        {!tooShort && search.isError && (
          <div className="px-4 py-4">
            <QueryErrorBanner error={search.error} />
          </div>
        )}

        {!tooShort && !search.isError && (
          <CommandList>
            {search.data === undefined ? (
              <p role="status" className="px-4 py-6 text-sm text-muted-foreground">
                Ricerca in corso…
              </p>
            ) : groups.length === 0 ? (
              /* State 1: nothing -- and the term is the one that was asked, not the one
                 currently being typed. */
              <p className="px-4 py-6 text-sm text-muted-foreground">
                Nessun risultato per «{search.debounced}»
              </p>
            ) : (
              /* State 2: what there is, with the real count and a way to the whole set. */
              groups.map((group, index) => {
                const goToList = seeAll(group.entity, search.debounced)
                return (
                  <Fragment key={group.entity}>
                    {index > 0 && <CommandSeparator />}
                    <CommandGroup heading={`${ENTITY_LABELS[group.entity]} · ${count(group)}`}>
                      {group.hits.map((hit) => (
                        <CommandItem key={hit.id} value={hit.id} onSelect={() => openHit(hit)}>
                          <span className="truncate">{hit.etichetta}</span>
                          {hit.sottotitolo && (
                            <span className="ml-2 truncate text-xs text-muted-foreground">
                              {hit.sottotitolo}
                            </span>
                          )}
                        </CommandItem>
                      ))}
                      {group.hits.length < group.totale && goToList !== null && (
                        <CommandItem
                          value={`vedi-tutti-${group.entity}`}
                          onSelect={() => {
                            close()
                            goToList()
                          }}
                        >
                          Vedi tutti {count(group)} {ENTITY_LABELS[group.entity].toLowerCase()}
                        </CommandItem>
                      )}
                    </CommandGroup>
                  </Fragment>
                )
              })
            )}
          </CommandList>
        )}
      </Command>
    </CommandDialog>
  )
}
