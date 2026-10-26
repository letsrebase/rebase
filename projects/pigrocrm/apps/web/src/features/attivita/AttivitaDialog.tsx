import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
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
import { useCreateAttivita } from '@/features/calendar/queries'
import { toProblem } from '@/lib/api'
import { linkColumns, type LinkValue } from './links'
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
 * at most one linked record. Reused by every screen that creates one, each prefilling what
 * it already knows (`defaults`), so no screen has its own small form that forgets a field.
 *
 * The form is a child rendered only while the dialog is open, so its state is born with
 * each opening: a dialog that kept the last title would offer to create the same task
 * twice.
 */
export function AttivitaDialog({
  open,
  onOpenChange,
  defaults = {},
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  defaults?: AttivitaDefaults
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Nuova attività</DialogTitle>
        </DialogHeader>
        <AttivitaForm defaults={defaults} onClose={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  )
}

function AttivitaForm({ defaults, onClose }: { defaults: AttivitaDefaults; onClose: () => void }) {
  const create = useCreateAttivita()
  const assignees = useAssignees()
  const [titolo, setTitolo] = useState('')
  const [scadenza, setScadenza] = useState(defaults.scadenza ?? '')
  const [note, setNote] = useState('')
  const [assegnataA, setAssegnataA] = useState(NOBODY)
  const [link, setLink] = useState<LinkValue | null>(defaults.link ?? null)
  const [problem, setProblem] = useState<string | null>(null)

  const trimmed = titolo.trim()

  function submit() {
    if (trimmed === '') return
    setProblem(null)
    create.mutate(
      {
        titolo: trimmed,
        note: note.trim() === '' ? null : note.trim(),
        scadenza: scadenza === '' ? null : scadenza,
        assegnata_a: assegnataA === NOBODY ? null : assegnataA,
        ...linkColumns(link),
        // Required by the generated schema (the field has a server-side default, which
        // openapi-typescript reads as «always present»); none is what a manual one has.
        custom_fields: {},
      },
      {
        onSuccess: () => {
          toast.success('Attività aggiunta')
          onClose()
        },
        onError: (error) => setProblem(toProblem(error).detail),
      },
    )
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

      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose}>
          Annulla
        </Button>
        <Button type="submit" disabled={trimmed === '' || create.isPending}>
          Aggiungi
        </Button>
      </DialogFooter>
    </form>
  )
}
