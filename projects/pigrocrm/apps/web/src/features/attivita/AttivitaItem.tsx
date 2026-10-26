import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import {
  useCancelAttivita,
  useCompleteAttivita,
  type Attivita,
} from '@/features/calendar/queries'
import { toProblem } from '@/lib/api'
import { useCanWrite } from '@/lib/auth'
import { formatIsoDateItalian } from '@/lib/dates'
import { AttivitaDialog } from './AttivitaDialog'
import { AttivitaMeta } from './AttivitaMeta'

/**
 * One attività as a list shows it, wherever the list is: the title (struck through once
 * it is closed), what it is for and about, its deadline when the list does not already
 * say it, and the three things a person does to it without opening anything: «Fatto»,
 * «Annulla» and «Modifica».
 *
 * «Annulla» is not a delete and not «fatto»: it records that the commitment stopped
 * mattering, which is the answer to *why* six months later. Deleting is «Archivia», in the
 * dialog «Modifica» opens.
 *
 * The edit dialog lives here, one per row and mounted only while open, so a list never has
 * to know which of its rows is being edited.
 */
export function AttivitaItem({
  attivita,
  showScadenza = false,
  hideLink = false,
}: {
  attivita: Attivita
  showScadenza?: boolean
  hideLink?: boolean
}) {
  const canWrite = useCanWrite()
  const complete = useCompleteAttivita()
  const cancel = useCancelAttivita()
  const [editing, setEditing] = useState(false)
  const open = attivita.stato === 'aperta'
  // One closure at a time: «Fatto» then «Annulla» in quick succession would race two writes
  // on the same row, and the second would answer with the first one's state.
  const pending = complete.isPending || cancel.isPending

  function close(mutation: typeof complete, done: string) {
    mutation.mutate(attivita.id, {
      onSuccess: () => toast.success(done),
      onError: (error) => toast.error(toProblem(error).detail),
    })
  }

  return (
    <li className="flex items-center justify-between gap-3">
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className={open ? undefined : 'line-through opacity-60'}>{attivita.titolo}</span>
        {showScadenza && (
          <span className="text-xs text-muted-foreground">
            {attivita.scadenza ? `Entro il ${formatIsoDateItalian(attivita.scadenza)}` : 'Senza scadenza'}
          </span>
        )}
        <AttivitaMeta attivita={attivita} hideLink={hideLink} />
      </span>
      {canWrite && (
        <span className="flex shrink-0 gap-2">
          {open && (
            <>
              <Button size="sm" variant="outline" disabled={pending} onClick={() => close(complete, 'Fatto')}>
                Fatto
              </Button>
              <Button size="sm" variant="outline" disabled={pending} onClick={() => close(cancel, 'Annullata')}>
                Annulla
              </Button>
            </>
          )}
          <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
            Modifica
          </Button>
        </span>
      )}
      {editing && <AttivitaDialog open onOpenChange={setEditing} attivita={attivita} />}
    </li>
  )
}
