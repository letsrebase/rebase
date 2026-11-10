import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { formatOccurredAt } from '@/components/activityLabels'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Card, CardContent } from '@rebase/ui/card'
import { LoadingState } from '@/components/LoadingState'
import { Textarea } from '@rebase/ui/textarea'
import { toProblem } from '@/lib/api'
import { useCan } from '@/lib/auth'
import {
  useAddNote,
  useDeleteNote,
  useNotes,
  useUpdateNote,
  type Note,
  type NoteEntityType,
  type NoteSource,
} from './queries'

/** The longest note the API accepts (`NOTE_MAX_LENGTH` in `core/notes/schemas.py`). */
const NOTE_MAX_LENGTH = 10_000

/**
 * Who a note reads as signed by when the row has no name to give: a note from before the
 * thread existed (`source` null), one a job or rebase wrote, or one whose author the CRM no
 * longer has a name for.
 */
const AUTHOR_FALLBACK: Record<NoteSource | 'previous', string> = {
  previous: 'Nota precedente',
  system: 'Sistema',
  rebase: 'rebase',
  mcp: 'Agente AI',
  web: 'Un membro',
}

/** The door a note came through, for the three that are worth saying. A note written in
 *  the browser is the ordinary one and carries nothing, as in the timeline. Quiet outline
 *  chips: a thread shows one per note, and the page's one strong action is elsewhere. */
const SOURCE_BADGE: Partial<Record<NoteSource, string>> = {
  mcp: 'via MCP',
  system: 'Sistema',
  rebase: 'rebase',
}

/**
 * One note: who wrote it, when, through which door, and «modificata» once its text was
 * changed (who and when in the title). A note the person may change can be edited in
 * place and deleted behind a confirmation; the others show neither button.
 */
function NoteItem({
  note,
  entityType,
  entityId,
}: {
  note: Note
  entityType: NoteEntityType
  entityId: string
}) {
  const canUpdate = useCan('update_note')
  const canDelete = useCan('delete_note')
  const update = useUpdateNote(entityType, entityId)
  const remove = useDeleteNote(entityType, entityId)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(note.text)

  const author = note.author ?? AUTHOR_FALLBACK[note.source ?? 'previous']
  const badge = note.source === null ? undefined : SOURCE_BADGE[note.source]
  const text = draft.trim()
  const error = update.error ?? remove.error
  // Which note a button is about, for a screen reader that lists the buttons of the page.
  const subject = `la nota di ${author} del ${formatOccurredAt(note.created_at)}`

  function startEditing() {
    setDraft(note.text)
    update.reset()
    remove.reset()
    setEditing(true)
  }

  function cancelEditing() {
    setEditing(false)
    setDraft(note.text)
    update.reset()
  }

  function save() {
    // Nothing changed: there is nothing to write, and the thread would record an edit.
    if (text === note.text) {
      cancelEditing()
      return
    }
    update.mutate(
      { noteId: note.id, text: text },
      {
        onSuccess: () => {
          setEditing(false)
          toast.success('Nota aggiornata')
        },
      },
    )
  }

  function confirmDelete() {
    if (!window.confirm('Eliminare questa nota? Resta traccia nella cronologia.')) return
    remove.mutate(note.id, { onSuccess: () => toast.success('Nota eliminata') })
  }

  return (
    <li className="space-y-1 py-4 first:pt-0 last:pb-0">
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">{author}</span>
        <time dateTime={note.created_at}>{formatOccurredAt(note.created_at)}</time>
        {badge !== undefined && badge !== author && <Badge variant="outline">{badge}</Badge>}
        {note.edited_at !== null && (
          <span>
            modificata{note.edited_by ? ` da ${note.edited_by}` : ''} il{' '}
            {formatOccurredAt(note.edited_at)}
          </span>
        )}
      </p>

      {editing ? (
        <form
          className="space-y-2"
          onSubmit={(event) => {
            event.preventDefault()
            if (text !== '') save()
          }}
        >
          <Textarea
            aria-label="Testo della nota"
            rows={3}
            maxLength={NOTE_MAX_LENGTH}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            disabled={update.isPending}
          />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" size="sm" disabled={text === '' || update.isPending}>
              {update.isPending ? 'Salvataggio…' : 'Salva'}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={update.isPending}
              onClick={cancelEditing}
            >
              Annulla
            </Button>
          </div>
        </form>
      ) : (
        <p className="whitespace-pre-wrap break-words text-sm">{note.text}</p>
      )}

      {note.editable && !editing && (canUpdate || canDelete) && (
        <div className="flex flex-wrap gap-2 pt-1">
          {canUpdate && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              aria-label={`Modifica ${subject}`}
              onClick={startEditing}
            >
              Modifica
            </Button>
          )}
          {canDelete && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              aria-label={`Elimina ${subject}`}
              disabled={remove.isPending}
              onClick={confirmDelete}
            >
              {remove.isPending ? 'Eliminazione…' : 'Elimina'}
            </Button>
          )}
        </div>
      )}

      {error !== null && (
        <p role="alert" className="text-sm text-destructive">
          {toProblem(error).detail}
        </p>
      )}
    </li>
  )
}

/**
 * The «Note» section of a deal's, a customer's or a person's page (REB-879): the one
 * thread where everything said about the record lives, newest first, each note signed,
 * dated and marked with the door it came through. The text never carries the author or
 * the date: the line above it does, and both are written by the CRM, never typed. A
 * person who may write adds a note; whoever wrote one (or an admin) edits and deletes it,
 * and the API says which in each row's `editable`. Every change is an entry of the
 * record's timeline. A person who may not write sees the thread and no form.
 */
export function NotesSection({
  entityType,
  entityId,
}: {
  entityType: NoteEntityType
  entityId: string
}) {
  const canAdd = useCan('add_note')
  const notes = useNotes(entityType, entityId)
  const add = useAddNote(entityType, entityId)
  const [draft, setDraft] = useState('')
  const text = draft.trim()
  const items = notes.data ?? []

  return (
    <Card>
      <CardContent className="pt-6">
        <h2 className="mb-3 font-semibold">
          Note
          {items.length > 0 && (
            <span className="ml-2 font-normal text-muted-foreground">{items.length}</span>
          )}
        </h2>

        {canAdd && (
          <form
            className="mb-4 space-y-2"
            onSubmit={(event) => {
              event.preventDefault()
              if (text === '') return
              add.mutate(text, {
                onSuccess: () => {
                  setDraft('')
                  toast.success('Nota aggiunta')
                },
              })
            }}
          >
            <Textarea
              aria-label="Nuova nota"
              rows={3}
              maxLength={NOTE_MAX_LENGTH}
              placeholder="Una telefonata fatta, un’impressione, una cosa da ricordare. Data e autore si aggiungono da soli."
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              disabled={add.isPending}
            />
            {add.error !== null && (
              <p role="alert" className="text-sm text-destructive">
                {toProblem(add.error).detail}
              </p>
            )}
            {/* Outline, not the filled variant: a page has one strong action, and it is
                the header's (design spec, «una sola cosa forte per schermata»). */}
            <Button
              type="submit"
              size="sm"
              variant="outline"
              disabled={text === '' || add.isPending}
            >
              {add.isPending ? 'Salvataggio…' : 'Aggiungi nota'}
            </Button>
          </form>
        )}

        {notes.isLoading ? (
          <LoadingState label="Caricamento note…" />
        ) : notes.isError ? (
          <QueryErrorBanner error={notes.error} />
        ) : items.length === 0 ? (
          <p className="text-muted-foreground">Nessuna nota, per ora.</p>
        ) : (
          <ul className="divide-y">
            {items.map((note) => (
              <NoteItem key={note.id} note={note} entityType={entityType} entityId={entityId} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
