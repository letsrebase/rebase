import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Label } from '@rebase/ui/label'
import { Textarea } from '@rebase/ui/textarea'
import { useAddNote } from '@/features/notes/queries'
import { RecordPicker } from '@/features/tasks/RecordPicker'
import type { LinkKind, LinkValue } from '@/features/tasks/links'
import { toProblem } from '@/lib/api'
import { useModules } from '@/lib/modules'

/** The records a note hangs from: a note is never on an invoice. */
const NOTE_KINDS: readonly LinkKind[] = ['customer', 'person', 'deal']

type NoteTarget = LinkValue & { kind: 'customer' | 'person' | 'deal' }

/**
 * «Nota» of the phone's quick add (REB-959): pick the customer, person or deal and write
 * the note, without leaving the page the person is on. The thread it lands in is the one the
 * record's own page shows (`useAddNote` invalidates it).
 */
export function QuickNote({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Nuova nota</DialogTitle>
          <DialogDescription>Scegli dove va la nota, poi scrivila.</DialogDescription>
        </DialogHeader>
        {/* Born with each opening, so a closed dialog never offers last time's text. */}
        <NoteForm onClose={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  )
}

function NoteForm({ onClose }: { onClose: () => void }) {
  const [link, setLink] = useState<LinkValue | null>(null)
  const dealsOn = useModules().on('deals')
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="quick-note-record">Record</Label>
        <RecordPicker
          id="quick-note-record"
          value={link}
          onChange={setLink}
          kinds={NOTE_KINDS}
          placeholder={
            dealsOn ? 'Cerca un cliente, una persona o un deal…' : 'Cerca un cliente o una persona…'
          }
        />
      </div>
      {link !== null && link.kind !== 'invoice' ? (
        // Keyed on the record: the mutation is built for one record, and a new pick starts
        // a new note.
        <NoteText key={`${link.kind}:${link.id}`} target={link as NoteTarget} onClose={onClose} />
      ) : null}
    </div>
  )
}

function NoteText({ target, onClose }: { target: NoteTarget; onClose: () => void }) {
  const add = useAddNote(target.kind, target.id)
  const [text, setText] = useState('')
  const trimmed = text.trim()
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        if (trimmed === '') return
        add.mutate(trimmed, {
          onSuccess: () => {
            toast.success('Nota aggiunta')
            onClose()
          },
        })
      }}
    >
      <div className="space-y-2">
        <Label htmlFor="quick-note-text">Nota</Label>
        <Textarea
          id="quick-note-text"
          value={text}
          rows={4}
          autoFocus
          onChange={(event) => setText(event.target.value)}
        />
      </div>
      {add.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {toProblem(add.error).detail}
        </p>
      ) : null}
      <DialogFooter>
        <Button type="submit" disabled={trimmed === '' || add.isPending}>
          Aggiungi la nota
        </Button>
      </DialogFooter>
    </form>
  )
}
