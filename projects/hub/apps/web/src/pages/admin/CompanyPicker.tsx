import { useState, type ReactNode, type RefObject } from 'react'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Loader } from '@rebase/ui/loader'
import { SEARCH_DEBOUNCE_MS, pageItems, useCursorList, useDebounce } from '@/lib/adminList'
import { admin, type Company } from '@/lib/api'
import { formatDate } from '@/lib/format'
import { keys } from '@/lib/query-keys'
import { LoadingState } from './list-kit'

const COMPANIES_PAGE = 50

/** The company request an admin picks at «Crea match» (`OpenEngagement`): a searchable list, a page at
 *  a time. */
export function CompanyPicker({
  selected,
  onSelect,
  searchRef,
}: {
  selected: Company | null
  onSelect: (company: Company) => void
  searchRef: RefObject<HTMLInputElement | null>
}) {
  const [q, setQ] = useState('')
  const term = useDebounce(q.trim(), SEARCH_DEBOUNCE_MS)
  // A page at a time, through the same cursor «Aziende» walks: an older request past the
  // first page stays one «Mostra altre» away (Greptile 4092036042).
  const { list: companies, items } = useCursorList({
    queryKey: keys.companiesForEngagement(term),
    fetchPage: (cursor) => admin.companies({ q: term || undefined, limit: COMPANIES_PAGE, cursor }),
    itemsOf: pageItems,
  })
  let list: ReactNode
  if (companies.isError) list = <p className="text-sm text-destructive">Non riesco a leggere le richieste.</p>
  else if (companies.isPending) list = <LoadingState label="Caricamento…" className="justify-start p-0" />
  else if (items.length === 0) list = <p className="text-sm text-muted-foreground">Nessuna richiesta trovata.</p>
  else
    list = (
      <ul className="space-y-2">
        {items.map((item) => {
          const closed = item.status === 'closed'
          const chosen = selected?.id === item.id
          return (
            <li key={item.id}>
              <Button
                type="button"
                variant={chosen ? 'default' : 'outline'}
                aria-pressed={chosen}
                disabled={closed}
                onClick={() => onSelect(item)}
                className={cn('h-auto w-full justify-start whitespace-normal py-2 text-left', closed && 'opacity-50')}
              >
                <span className="flex flex-col items-start gap-0.5">
                  <span className="font-medium">
                    {item.company_name}
                    {closed && ' · chiusa'}
                  </span>
                  <span className="text-xs">
                    {item.contact_person} · {item.requested_role} · dal {formatDate(item.period_from)}
                  </span>
                </span>
              </Button>
            </li>
          )
        })}
      </ul>
    )
  return (
    <>
      <div className="space-y-1.5">
        <Label htmlFor="engagement-company-q">Cerca una richiesta</Label>
        <Input
          ref={searchRef}
          id="engagement-company-q"
          type="search"
          maxLength={200}
          placeholder="Azienda, referente, email…"
          value={q}
          onChange={(event) => setQ(event.target.value)}
        />
      </div>
      {list}
      {companies.hasNextPage && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void companies.fetchNextPage()}
          disabled={companies.isFetchingNextPage}
        >
          {companies.isFetchingNextPage ? (
            <>
              <Loader className="mr-2 size-4" />
              Caricamento…
            </>
          ) : (
            'Mostra altre'
          )}
        </Button>
      )}
    </>
  )
}
