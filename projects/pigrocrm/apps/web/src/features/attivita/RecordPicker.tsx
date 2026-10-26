import { X } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { MIN_TERM_LENGTH, useGlobalSearch, type SearchEntity } from '@/features/search/queries'
import { LINK_KIND_LABELS, type LinkKind, type LinkValue } from './links'
import { useRecordLabel } from './queries'

/** The four kinds an attività can be linked to, out of the five the global search knows:
 *  a document belongs to a customer or a deal and is not a thing a task hangs from. */
const LINKABLE: readonly SearchEntity[] = ['customer', 'person', 'deal', 'invoice']

function isLinkable(entity: SearchEntity): entity is LinkKind {
  return LINKABLE.includes(entity)
}

/**
 * Picks at most one record out of the four kinds an attività may hang from.
 *
 * Built on the global search rather than on four dropdowns: a space has thousands of
 * customers and invoices, and «scegli dall'elenco» over `limit: 200` is a list that
 * silently stops at the 200th. Typing three letters is the same gesture as the palette's.
 * What is chosen is shown by name (`useRecordLabel`), with a button to take it off: a link
 * is optional, and an attività with none is a valid one.
 */
export function RecordPicker({
  id,
  value,
  onChange,
}: {
  id: string
  value: LinkValue | null
  onChange: (next: LinkValue | null) => void
}) {
  const [term, setTerm] = useState('')
  const search = useGlobalSearch(term)
  const label = useRecordLabel(value)

  if (value !== null) {
    return (
      <div className="flex items-center justify-between gap-2 border px-3 py-2 text-sm">
        <span className="min-w-0 truncate">
          <span className="text-muted-foreground">{LINK_KIND_LABELS[value.kind]} · </span>
          {label ?? 'Caricamento…'}
        </span>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label="Scollega"
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

  const hits = (search.data?.gruppi ?? []).flatMap((group) =>
    isLinkable(group.entity) ? group.hits : [],
  )
  const tooShort = search.debounced.length < MIN_TERM_LENGTH

  return (
    <div className="space-y-2">
      <Input
        id={id}
        value={term}
        placeholder="Cerca un cliente, una persona, un deal o una fattura…"
        onChange={(event) => setTerm(event.target.value)}
      />
      {!tooShort && (
        <ul aria-label="Risultati" className="max-h-44 divide-y overflow-y-auto border text-sm">
          {search.isError ? (
            <li className="px-3 py-2 text-destructive">La ricerca non è disponibile.</li>
          ) : search.data === undefined ? (
            <li className="px-3 py-2 text-muted-foreground">Ricerca in corso…</li>
          ) : hits.length === 0 ? (
            <li className="px-3 py-2 text-muted-foreground">
              Nessun risultato per «{search.debounced}»
            </li>
          ) : (
            hits.map((hit) => (
              <li key={hit.id}>
                <button
                  type="button"
                  className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-muted focus-visible:bg-muted focus-visible:outline-none"
                  onClick={() => {
                    setTerm('')
                    onChange({ kind: hit.entity as LinkKind, id: hit.id })
                  }}
                >
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {LINK_KIND_LABELS[hit.entity as LinkKind]}
                  </span>
                  <span className="truncate">{hit.etichetta}</span>
                </button>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  )
}
