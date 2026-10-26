import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { Textarea } from '@rebase/ui/textarea'
import {
  useArchiveAttivita,
  useCreateAttivita,
  useReopenAttivita,
  useRestoreAttivita,
  useUpdateAttivita,
  type Attivita,
  type AttivitaUpdateBody as UpdateBody,
} from '@/features/calendar/queries'
import { toProblem } from '@/lib/api'
import { linkColumns, linkOf, sameLink, type LinkValue } from './links'
import { useAssignees } from './queries'
import { RecordPicker } from './RecordPicker'

/** The assignee select cannot hold an empty string (Radix refuses it), and no user id is
 *  this word. `null` on the wire is «whoever opens the list», which reads as nobody in
 *  particular. */
const NOBODY = 'nessuno'

/** What a caller may fill in beforehand: the calendar's day, a record page's own record. */
export interface AttivitaDefaults {
  scadenza?: string
  link?: LinkValue | null
}

/**
 * The one dialog an attività is written in: title, optional deadline, notes, assignee and
 * at most one linked record. Reused by every screen that creates or opens one, each
 * prefilling what it already knows (`defaults`), so no screen has its own small form that
 * forgets a field.
 *
 * Given an `attivita` it is the edit form of that row, with the two things that only make
 * sense on an existing one: «Riapri» for a closed one and «Archivia» for a row made by
 * mistake (or «Ripristina», when the row is already archived: `archived`, since the read
 * shape does not say). Completing and cancelling stay where they were, on the lists.
 *
 * The form is a child rendered only while the dialog is open, so its state is born with
 * each opening: a dialog that kept the last title would offer to create the same task
 * twice.
 */
export function AttivitaDialog({
  open,
  onOpenChange,
  attivita,
  archived = false,
  defaults = {},
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  attivita?: Attivita
  archived?: boolean
  defaults?: AttivitaDefaults
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{attivita ? 'Modifica attività' : 'Nuova attività'}</DialogTitle>
          <DialogDescription>
            Scadenza, note, assegnatario e record collegato sono facoltativi.
          </DialogDescription>
        </DialogHeader>
        <AttivitaForm
          attivita={attivita}
          archived={archived}
          defaults={defaults}
          onClose={() => onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  )
}

function AttivitaForm({
  attivita,
  archived,
  defaults,
  onClose,
}: {
  attivita: Attivita | undefined
  archived: boolean
  defaults: AttivitaDefaults
  onClose: () => void
}) {
  const create = useCreateAttivita()
  const update = useUpdateAttivita()
  const reopen = useReopenAttivita()
  const archive = useArchiveAttivita()
  const restore = useRestoreAttivita()
  const assignees = useAssignees()

  const initialLink = attivita ? linkOf(attivita) : (defaults.link ?? null)
  const [titolo, setTitolo] = useState(attivita?.titolo ?? '')
  const [scadenza, setScadenza] = useState(attivita?.scadenza ?? defaults.scadenza ?? '')
  const [note, setNote] = useState(attivita?.note ?? '')
  const [assegnataA, setAssegnataA] = useState(attivita?.assegnata_a ?? NOBODY)
  const [link, setLink] = useState<LinkValue | null>(initialLink)
  const [problem, setProblem] = useState<string | null>(null)

  const trimmed = titolo.trim()
  const busy =
    create.isPending ||
    update.isPending ||
    reopen.isPending ||
    archive.isPending ||
    restore.isPending

  /** Runs a mutation, closing the dialog with `done` on success and showing the API's own
   *  sentence on failure: the refusals of a row that changed under us are worth reading. */
  function run<T>(
    mutate: (options: { onSuccess: (data: T) => void; onError: (error: unknown) => void }) => void,
    done: string,
  ) {
    setProblem(null)
    mutate({
      onSuccess: () => {
        toast.success(done)
        onClose()
      },
      onError: (error) => setProblem(toProblem(error).detail),
    })
  }

  function submit() {
    if (trimmed === '') return
    const cleanNote = note.trim() === '' ? null : note.trim()
    const cleanScadenza = scadenza === '' ? null : scadenza
    const cleanAssignee = assegnataA === NOBODY ? null : assegnataA

    if (!attivita) {
      run(
        (options) =>
          create.mutate(
            {
              titolo: trimmed,
              note: cleanNote,
              scadenza: cleanScadenza,
              assegnata_a: cleanAssignee,
              ...linkColumns(link),
              // Required by the generated schema (the field has a server-side default,
              // which openapi-typescript reads as «always present»); none is what a
              // manual one has.
              custom_fields: {},
            },
            options,
          ),
        'Attività aggiunta',
      )
      return
    }

    // Only what changed: the timeline records which fields a PATCH named, and a save that
    // names all of them says the person rewrote everything.
    const edits: Omit<UpdateBody, 'scadenza_da_rimuovere' | 'riferimento_da_rimuovere'> = {}
    if (trimmed !== attivita.titolo) edits.titolo = trimmed
    if (cleanNote !== attivita.note) edits.note = cleanNote
    if (cleanScadenza !== attivita.scadenza) edits.scadenza = cleanScadenza
    if (cleanAssignee !== attivita.assegnata_a) edits.assegnata_a = cleanAssignee
    // All four columns together: moving a task from a customer to a deal is «set deal_id
    // and clear customer_id», and the service checks the result, not the patch.
    if (!sameLink(link, initialLink)) Object.assign(edits, linkColumns(link))

    if (Object.keys(edits).length === 0) {
      onClose()
      return
    }
    // The two flags are required by the generated schema (server-side defaults, read as
    // «always present»): both false, because a null is how this form clears a field.
    const changes: UpdateBody = {
      ...edits,
      scadenza_da_rimuovere: false,
      riferimento_da_rimuovere: false,
    }
    run((options) => update.mutate({ id: attivita.id, changes }, options), 'Attività salvata')
  }

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        submit()
      }}
    >
      <div className="space-y-1">
        <Label htmlFor="attivita-titolo">Titolo</Label>
        <Input
          id="attivita-titolo"
          autoFocus
          value={titolo}
          maxLength={200}
          placeholder="Che cosa c’è da fare"
          onChange={(event) => setTitolo(event.target.value)}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor="attivita-scadenza">Scadenza</Label>
          <Input
            id="attivita-scadenza"
            type="date"
            value={scadenza}
            onChange={(event) => setScadenza(event.target.value)}
          />
          <p className="text-xs text-muted-foreground">Vuota: resta tra le attività senza scadenza.</p>
        </div>
        <div className="space-y-1">
          <Label htmlFor="attivita-assegnata">Assegnata a</Label>
          <Select value={assegnataA} onValueChange={setAssegnataA}>
            <SelectTrigger id="attivita-assegnata">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NOBODY}>Nessuno in particolare</SelectItem>
              {assignees.options.map((person) => (
                <SelectItem key={person.id} value={person.id}>
                  {person.id === assignees.me ? `${person.nome} (io)` : person.nome}
                </SelectItem>
              ))}
              {/* The current assignee when this list cannot name them (a colleague seen by
                  somebody who is not an admin, or an account since deactivated): the row
                  keeps its value until somebody chooses another. */}
              {attivita?.assegnata_a &&
                !assignees.options.some((person) => person.id === attivita.assegnata_a) && (
                  <SelectItem value={attivita.assegnata_a}>
                    {assignees.nameOf(attivita.assegnata_a)}
                  </SelectItem>
                )}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="space-y-1">
        <Label htmlFor="attivita-note">Note</Label>
        <Textarea
          id="attivita-note"
          value={note}
          rows={3}
          onChange={(event) => setNote(event.target.value)}
        />
      </div>

      <div className="space-y-1">
        <Label htmlFor="attivita-collegamento">Collegata a</Label>
        <RecordPicker id="attivita-collegamento" value={link} onChange={setLink} />
      </div>

      {problem !== null && (
        <p role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      )}

      <DialogFooter className="sm:justify-between">
        <div className="flex gap-2">
          {attivita && archived && (
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => run((options) => restore.mutate(attivita.id, options), 'Attività ripristinata')}
            >
              Ripristina
            </Button>
          )}
          {attivita && !archived && attivita.stato !== 'aperta' && (
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => run((options) => reopen.mutate(attivita.id, options), 'Attività riaperta')}
            >
              Riapri
            </Button>
          )}
          {attivita && !archived && (
            <Button
              type="button"
              variant="destructive"
              disabled={busy}
              onClick={() => run((options) => archive.mutate(attivita.id, options), 'Attività archiviata')}
            >
              Archivia
            </Button>
          )}
        </div>
        <div className="flex gap-2">
          <Button type="button" variant="outline" onClick={onClose}>
            Annulla
          </Button>
          {!archived && (
            <Button type="submit" disabled={trimmed === '' || busy}>
              {attivita ? 'Salva' : 'Aggiungi'}
            </Button>
          )}
        </div>
      </DialogFooter>
    </form>
  )
}
