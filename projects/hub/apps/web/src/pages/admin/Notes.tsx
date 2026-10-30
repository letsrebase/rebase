import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Textarea } from '@rebase/ui/textarea'
import { ApiError, admin, type Note, type ThreadKind } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import { noteSourceLabel } from '@/lib/notes'

const NOTE_MAX_LENGTH = 4000

function failureOf(error: unknown, fallback: string): string | null {
  if (error instanceof ApiError) return error.message
  return error ? fallback : null
}

/** One note: who wrote it, when, through which door, and «modificata da X il …» once an
 *  admin changed it. An admin's note can be edited in place and deleted behind a second
 *  click; the person's and the hub's own events (`modificabile: false`) show neither. */
function NoteItem({
  note,
  onEdited,
  onDeleted,
}: {
  note: Note
  onEdited: (edited: Note) => void
  onDeleted: (id: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [asking, setAsking] = useState(false)
  const [draft, setDraft] = useState(note.testo)
  const edit = useMutation({
    mutationFn: (testo: string) => admin.editNote(note.id, testo),
    onSuccess: (edited) => {
      setEditing(false)
      onEdited(edited)
    },
  })
  const remove = useMutation({
    mutationFn: () => admin.deleteNote(note.id),
    onSuccess: () => onDeleted(note.id),
  })
  const source = noteSourceLabel(note.fonte)
  const text = draft.trim()
  const failure =
    failureOf(edit.error, 'Non riesco a salvare la nota.') ??
    failureOf(remove.error, 'Non riesco a eliminare la nota.')

  return (
    <li className="space-y-1 py-3 first:pt-0 last:pb-0">
      <p className="flex flex-wrap items-baseline gap-x-2 break-words text-xs text-muted-foreground">
        <span className="font-medium text-foreground">{note.autore}</span>
        <time dateTime={note.created_at}>{formatDateTime(note.created_at)}</time>
        {source && <Badge variant="pill">{source}</Badge>}
        {note.modificata_at && (
          <span>
            modificata{note.modificata_da ? ` da ${note.modificata_da}` : ''} il{' '}
            <time dateTime={note.modificata_at}>{formatDateTime(note.modificata_at)}</time>
          </span>
        )}
      </p>
      {editing ? (
        <form
          className="space-y-2"
          onSubmit={(event) => {
            event.preventDefault()
            if (text) edit.mutate(text)
          }}
        >
          <Textarea
            aria-label="Testo della nota"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            rows={3}
            maxLength={NOTE_MAX_LENGTH}
            disabled={edit.isPending}
          />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" size="sm" disabled={!text || edit.isPending}>
              {edit.isPending ? 'Salvo…' : 'Salva'}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={edit.isPending}
              onClick={() => {
                setEditing(false)
                setDraft(note.testo)
                edit.reset()
              }}
            >
              Annulla
            </Button>
          </div>
        </form>
      ) : (
        <p className="whitespace-pre-wrap break-words">{note.testo}</p>
      )}
      {note.modificabile && !editing && (
        <div className="flex flex-wrap items-center gap-2 pt-1">
          {asking ? (
            <>
              <span className="text-sm">Eliminare questa nota?</span>
              <Button
                type="button"
                size="sm"
                variant="destructive"
                disabled={remove.isPending}
                onClick={() => remove.mutate()}
              >
                {remove.isPending ? 'Elimino…' : 'Conferma'}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={remove.isPending}
                onClick={() => setAsking(false)}
              >
                Indietro
              </Button>
            </>
          ) : (
            <>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => {
                  setDraft(note.testo)
                  setEditing(true)
                }}
              >
                Modifica
              </Button>
              <Button type="button" size="sm" variant="outline" onClick={() => setAsking(true)}>
                Elimina
              </Button>
            </>
          )}
        </div>
      )}
      {failure && (
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      )}
    </li>
  )
}

/**
 * The notes of a freelancer or a company (REB-856): the one thread where everything said
 * about the row lives, newest first, each note signed, dated and marked with the door it
 * came through (this site, an agent over MCP, the person's own change). The text never
 * carries the date or the author, since the line above it does. An admin adds, edits and
 * deletes their own notes; every change is in the record's trail. The list belongs to the
 * detail page's cache, so the page hands it in and hears back what changed (`onChange`)
 * rather than the component keeping a second copy that could drift from the row.
 */
export function Notes({
  kind,
  id,
  notes,
  onChange,
}: {
  kind: ThreadKind
  id: string
  notes: Note[]
  /** An updater applied by the page to the thread currently in its cache, never to the
   *  `notes` prop captured when a mutation started: a slow delete must not bring a note
   *  back because an add resolved first. The page also refreshes the trail. */
  onChange: (update: (previous: Note[]) => Note[]) => void
}) {
  const [draft, setDraft] = useState('')
  const add = useMutation({
    mutationFn: (testo: string) => admin.addNote(kind, id, testo),
    onSuccess: (created) => {
      setDraft('')
      onChange((previous) => [created, ...previous])
    },
  })
  const text = draft.trim()
  const failure = failureOf(add.error, 'Non riesco a salvare la nota.')

  return (
    <section aria-label="Note" className="space-y-3 border p-4">
      <h2 className="text-sm font-medium">
        Note
        {notes.length > 0 && <span className="ml-2 font-normal text-muted-foreground">{notes.length}</span>}
      </h2>
      <form
        className="space-y-2"
        onSubmit={(event) => {
          event.preventDefault()
          if (text) add.mutate(text)
        }}
      >
        <Textarea
          aria-label="Nuova nota"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          rows={3}
          maxLength={NOTE_MAX_LENGTH}
          placeholder="Una telefonata fatta, un'impressione, una cosa da ricordare. Data e autore si aggiungono da soli."
          disabled={add.isPending}
        />
        {failure && (
          <p role="alert" className="text-sm text-destructive">
            {failure}
          </p>
        )}
        <Button type="submit" size="sm" disabled={!text || add.isPending}>
          {add.isPending ? 'Aggiungo…' : 'Aggiungi nota'}
        </Button>
      </form>
      {notes.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nessuna nota, per ora.</p>
      ) : (
        <ol className="divide-y text-sm">
          {notes.map((note) => (
            <NoteItem
              key={note.id}
              note={note}
              onEdited={(edited) => onChange((previous) => previous.map((item) => (item.id === edited.id ? edited : item)))}
              onDeleted={(noteId) => onChange((previous) => previous.filter((item) => item.id !== noteId))}
            />
          ))}
        </ol>
      )}
    </section>
  )
}
