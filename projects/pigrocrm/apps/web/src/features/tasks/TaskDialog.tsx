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
  useArchiveTask,
  useCreateTask,
  useReopenTask,
  useRestoreTask,
  useUpdateTask,
  type Task,
  type TaskUpdateBody as UpdateBody,
} from '@/features/calendar/queries'
import { toProblem } from '@/lib/api'
import { useCanWrite } from '@/lib/auth'
import { linkColumns, linkOf, sameLink, type LinkValue } from './links'
import { useAssignees } from './queries'
import { RecordPicker } from './RecordPicker'

/** The assignee select cannot hold an empty string (Radix refuses it), and no user id is
 *  this word. `null` on the wire is «whoever opens the list», which reads as nobody in
 *  particular. */
const NOBODY = 'none'

/** What a caller may fill in beforehand: the calendar's day, a record page's own record. */
export interface TaskDefaults {
  due_date?: string
  link?: LinkValue | null
}

/**
 * The one dialog a task is written in: title, optional deadline, notes, assignee and
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
export function TaskDialog({
  open,
  onOpenChange,
  task,
  archived = false,
  defaults = {},
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  task?: Task
  archived?: boolean
  defaults?: TaskDefaults
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{task ? 'Modifica attività' : 'Nuova attività'}</DialogTitle>
          <DialogDescription>
            Scadenza, note, assegnatario e record collegato sono facoltativi.
          </DialogDescription>
        </DialogHeader>
        <TaskForm
          task={task}
          archived={archived}
          defaults={defaults}
          onClose={() => onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  )
}

function TaskForm({
  task,
  archived,
  defaults,
  onClose,
}: {
  task: Task | undefined
  archived: boolean
  defaults: TaskDefaults
  onClose: () => void
}) {
  const create = useCreateTask()
  const update = useUpdateTask()
  const reopen = useReopenTask()
  const archive = useArchiveTask()
  const restore = useRestoreTask()
  const assignees = useAssignees()
  const readOnly = !useCanWrite()

  const initialLink = task ? linkOf(task) : (defaults.link ?? null)
  const [title, setTitle] = useState(task?.title ?? '')
  const [due_date, setDueDate] = useState(task?.due_date ?? defaults.due_date ?? '')
  const [note, setNote] = useState(task?.note ?? '')
  const [assignedTo, setAssignedTo] = useState(task?.assigned_to ?? NOBODY)
  const [link, setLink] = useState<LinkValue | null>(initialLink)
  const [problem, setProblem] = useState<string | null>(null)

  const trimmed = title.trim()
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
    const cleanDueDate = due_date === '' ? null : due_date
    const cleanAssignee = assignedTo === NOBODY ? null : assignedTo

    if (!task) {
      run(
        (options) =>
          create.mutate(
            {
              title: trimmed,
              note: cleanNote,
              due_date: cleanDueDate,
              assigned_to: cleanAssignee,
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
    const edits: Omit<UpdateBody, 'due_date_to_remove' | 'reference_to_remove'> = {}
    if (trimmed !== task.title) edits.title = trimmed
    if (cleanNote !== task.note) edits.note = cleanNote
    if (cleanDueDate !== task.due_date) edits.due_date = cleanDueDate
    if (cleanAssignee !== task.assigned_to) edits.assigned_to = cleanAssignee
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
      due_date_to_remove: false,
      reference_to_remove: false,
    }
    run((options) => update.mutate({ id: task.id, changes }, options), 'Attività salvata')
  }

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        submit()
      }}
    >
      {/* A reader opens the same dialog, to read: every field is disabled and the buttons
          that write are not drawn (a search hit must open for anybody who can search). */}
      <fieldset disabled={readOnly} className="min-w-0 space-y-4">
      <div className="space-y-1">
        <Label htmlFor="task-title">Titolo</Label>
        <Input
          id="task-title"
          autoFocus
          value={title}
          maxLength={200}
          placeholder="Che cosa c’è da fare"
          onChange={(event) => setTitle(event.target.value)}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor="task-due-date">Scadenza</Label>
          <Input
            id="task-due-date"
            type="date"
            value={due_date}
            onChange={(event) => setDueDate(event.target.value)}
          />
          <p className="text-xs text-muted-foreground">Vuota: resta tra le attività senza scadenza.</p>
        </div>
        <div className="space-y-1">
          <Label htmlFor="task-assignee">Assegnata a</Label>
          <Select value={assignedTo} onValueChange={setAssignedTo}>
            <SelectTrigger id="task-assignee">
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
              {task?.assigned_to &&
                !assignees.options.some((person) => person.id === task.assigned_to) && (
                  <SelectItem value={task.assigned_to}>
                    {assignees.nameOf(task.assigned_to)}
                  </SelectItem>
                )}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="space-y-1">
        <Label htmlFor="task-note">Note</Label>
        <Textarea
          id="task-note"
          value={note}
          rows={3}
          onChange={(event) => setNote(event.target.value)}
        />
      </div>

      <div className="space-y-1">
        <Label htmlFor="task-link">Collegata a</Label>
        <RecordPicker id="task-link" value={link} onChange={setLink} />
      </div>
      </fieldset>
      {problem !== null && (
        <p role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      )}

      <DialogFooter className="sm:justify-between">
        <div className="flex gap-2">
          {task && archived && !readOnly && (
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => run((options) => restore.mutate(task.id, options), 'Attività ripristinata')}
            >
              Ripristina
            </Button>
          )}
          {task && !archived && !readOnly && task.status !== 'open' && (
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => run((options) => reopen.mutate(task.id, options), 'Attività riaperta')}
            >
              Riapri
            </Button>
          )}
          {task && !archived && !readOnly && (
            <Button
              type="button"
              variant="destructive"
              disabled={busy}
              onClick={() => run((options) => archive.mutate(task.id, options), 'Attività archiviata')}
            >
              Archivia
            </Button>
          )}
        </div>
        <div className="flex gap-2">
          <Button type="button" variant="outline" onClick={onClose}>
            {readOnly ? 'Chiudi' : 'Annulla'}
          </Button>
          {!archived && !readOnly && (
            <Button type="submit" disabled={trimmed === '' || busy}>
              {task ? 'Salva' : 'Aggiungi'}
            </Button>
          )}
        </div>
      </DialogFooter>
    </form>
  )
}
