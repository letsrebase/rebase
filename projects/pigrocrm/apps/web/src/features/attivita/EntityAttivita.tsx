import { Plus } from 'lucide-react'
import { useState } from 'react'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Skeleton } from '@rebase/ui/skeleton'
import { useCanWrite } from '@/lib/auth'
import { AttivitaDialog } from './AttivitaDialog'
import { AttivitaItem } from './AttivitaItem'
import { LINK_COLUMN, type LinkValue } from './links'
import { useAttivitaList } from './queries'

/**
 * The attività of one record: the open ones first, then the closed ones, each with the
 * actions of any other list, and a button that opens the creation dialog already linked
 * to this record.
 *
 * Not the record's timeline. The timeline is the history of what happened to it; this is
 * what somebody still has to do about it, and the page the two sit on says so in both
 * names. Only the attività linked to this record itself are listed: a deal's tasks do not
 * appear on its customer's page, since the link is one record by design and a roll-up
 * would answer a different question than «what is attached here».
 *
 * `hideLink` on the rows because every one of them would repeat the page's own title.
 */
export function EntityAttivita({ link }: { link: LinkValue }) {
  const canWrite = useCanWrite()
  const [creating, setCreating] = useState(false)
  // Open and closed are asked for separately: one list of everything, cut at 200, could
  // fill with closed history and leave out what is still to be done.
  const column = { [LINK_COLUMN[link.kind]]: link.id }
  const list = useAttivitaList({ ...column, stato: 'aperta', sort: 'scadenza', limit: 200 })
  const completed = useAttivitaList({ ...column, stato: 'completata', sort: 'scadenza', dir: 'desc', limit: 50 })
  const cancelled = useAttivitaList({ ...column, stato: 'annullata', sort: 'scadenza', dir: 'desc', limit: 50 })

  const open = list.data?.items ?? []
  const closed = [...(completed.data?.items ?? []), ...(cancelled.data?.items ?? [])]
  const items = [...open, ...closed]

  return (
    <section aria-labelledby="attivita-del-record" className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h2 id="attivita-del-record" className="font-semibold">
          Attività
        </h2>
        {canWrite && (
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus className="mr-2 size-4" />
            Nuova attività
          </Button>
        )}
      </div>

      {list.isError || completed.isError || cancelled.isError ? (
        <QueryErrorBanner error={list.error ?? completed.error ?? cancelled.error} />
      ) : list.isPending || completed.isPending || cancelled.isPending ? (
        <Skeleton className="h-12 w-full" />
      ) : items.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nessuna attività collegata.</p>
      ) : (
        <>
          <ul className="space-y-3 text-sm">
            {open.map((item) => (
              <AttivitaItem key={item.id} attivita={item} showScadenza hideLink />
            ))}
          </ul>
          {closed.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-sm font-medium text-muted-foreground">Chiuse</h3>
              <ul className="space-y-3 text-sm">
                {closed.map((item) => (
                  <AttivitaItem key={item.id} attivita={item} showScadenza hideLink />
                ))}
              </ul>
            </div>
          )}
          {(list.data?.next_cursor != null ||
            completed.data?.next_cursor != null ||
            cancelled.data?.next_cursor != null) && (
            <p className="text-sm text-muted-foreground">
              Non sono mostrate tutte: le altre sono nella pagina Attività.
            </p>
          )}
        </>
      )}

      <AttivitaDialog open={creating} onOpenChange={setCreating} defaults={{ link }} />
    </section>
  )
}
