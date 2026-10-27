import { Plus } from 'lucide-react'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Card, CardContent } from '@rebase/ui/card'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Skeleton } from '@rebase/ui/skeleton'
import { Textarea } from '@rebase/ui/textarea'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import { useCan } from '@/lib/auth'
import {
  useClauseNotes,
  useCreateClauseNote,
  useUpdateClauseNote,
  type ClauseNote,
  type ClauseNoteCreateBody,
  type ClauseNoteUpdateBody,
} from './queries'

interface NoteFormValues {
  riferimento_clausola: string
  testo_letterale: string
  interpretazione_adottata: string
  note: string
}

const EMPTY_FORM: NoteFormValues = {
  riferimento_clausola: '',
  testo_letterale: '',
  interpretazione_adottata: '',
  note: '',
}

/** The fields the form draws, so an error on any other one is still read somewhere. */
const FORM_FIELDS = Object.keys(EMPTY_FORM)

function toFormValues(note: ClauseNote): NoteFormValues {
  return {
    riferimento_clausola: note.riferimento_clausola,
    testo_letterale: note.testo_letterale,
    interpretazione_adottata: note.interpretazione_adottata,
    note: note.note ?? '',
  }
}

/** An empty optional note is no note: the API stores `null`, never an empty string. */
function toNullable(text: string): string | null {
  return text === '' ? null : text
}

function toCreateBody(values: NoteFormValues): ClauseNoteCreateBody {
  return {
    riferimento_clausola: values.riferimento_clausola,
    testo_letterale: values.testo_letterale,
    interpretazione_adottata: values.interpretazione_adottata,
    note: toNullable(values.note),
  }
}

/** Only what differs from the stored note, so a patch never rewrites what nobody touched.
 *  The three required fields are sent as typed and the server refuses a blank one. */
function toUpdateBody(values: NoteFormValues, note: ClauseNote): ClauseNoteUpdateBody {
  const body: ClauseNoteUpdateBody = {}
  if (values.riferimento_clausola !== note.riferimento_clausola) {
    body.riferimento_clausola = values.riferimento_clausola
  }
  if (values.testo_letterale !== note.testo_letterale) body.testo_letterale = values.testo_letterale
  if (values.interpretazione_adottata !== note.interpretazione_adottata) {
    body.interpretazione_adottata = values.interpretazione_adottata
  }
  if (toNullable(values.note) !== note.note) body.note = toNullable(values.note)
  return body
}

interface DialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  contractId: string
  /** The note being edited, or absent for a new one. */
  note?: ClauseNote
}

function ClauseNoteDialog({ open, onOpenChange, contractId, note }: DialogProps) {
  const isCreate = note === undefined
  const initial = note === undefined ? EMPTY_FORM : toFormValues(note)
  const [values, setValues] = useState<NoteFormValues>(initial)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const create = useCreateClauseNote(contractId)
  const update = useUpdateClauseNote(contractId, note?.id ?? '')

  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setValues(initial)
      setProblem(null)
    }
  }

  function set<K extends keyof NoteFormValues>(key: K, value: NoteFormValues[K]) {
    setValues((previous) => ({ ...previous, [key]: value }))
  }

  function submit() {
    setProblem(null)
    const done = (message: string) => () => {
      onOpenChange(false)
      toast.success(message)
    }
    if (isCreate) {
      create.mutate(toCreateBody(values), {
        onSuccess: done('Nota aggiunta'),
        onError: (error) => setProblem(toProblem(error)),
      })
      return
    }
    const body = toUpdateBody(values, note)
    // Nothing changed: there is nothing to write, and an empty patch is not a request.
    if (Object.keys(body).length === 0) {
      onOpenChange(false)
      return
    }
    update.mutate(body, {
      onSuccess: done('Nota aggiornata'),
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  const busy = create.isPending || update.isPending
  const fieldError = problem ? fieldErrorFrom(problem) : null
  const errorOn = (key: string) =>
    fieldError?.field === key ? (
      <p className="text-sm text-destructive">{fieldError.message}</p>
    ) : null
  // Whatever no control answers for (a model-level error, a 404 on a note that is gone)
  // still has to be read, or saving would seem to do nothing.
  const elsewhere =
    problem !== null && !(fieldError !== null && FORM_FIELDS.includes(fieldError.field))
      ? fieldError === null
        ? problem.detail
        : fieldError.message
      : null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isCreate ? 'Nuova nota sulla clausola' : 'Modifica nota'}</DialogTitle>
        </DialogHeader>

        <div className="space-y-2">
          <Label htmlFor="clause-riferimento">Clausola</Label>
          <Input
            id="clause-riferimento"
            placeholder="Art. 8.3 Rinnovo"
            value={values.riferimento_clausola}
            onChange={(event) => set('riferimento_clausola', event.target.value)}
            aria-invalid={fieldError?.field === 'riferimento_clausola'}
          />
          {errorOn('riferimento_clausola')}
        </div>

        <div className="space-y-2">
          <Label htmlFor="clause-testo">Testo letterale</Label>
          <Textarea
            id="clause-testo"
            rows={4}
            value={values.testo_letterale}
            onChange={(event) => set('testo_letterale', event.target.value)}
            aria-invalid={fieldError?.field === 'testo_letterale'}
          />
          <p className="text-xs text-muted-foreground">Le parole del contratto, copiate come sono.</p>
          {errorOn('testo_letterale')}
        </div>

        <div className="space-y-2">
          <Label htmlFor="clause-interpretazione">Interpretazione adottata</Label>
          <Textarea
            id="clause-interpretazione"
            rows={4}
            value={values.interpretazione_adottata}
            onChange={(event) => set('interpretazione_adottata', event.target.value)}
            aria-invalid={fieldError?.field === 'interpretazione_adottata'}
          />
          <p className="text-xs text-muted-foreground">Come hai deciso di leggerla.</p>
          {errorOn('interpretazione_adottata')}
        </div>

        <div className="space-y-2">
          <Label htmlFor="clause-note">Note (facoltative)</Label>
          <Textarea
            id="clause-note"
            rows={3}
            value={values.note}
            onChange={(event) => set('note', event.target.value)}
            aria-invalid={fieldError?.field === 'note'}
          />
          {errorOn('note')}
        </div>

        {elsewhere !== null && <p className="text-sm text-destructive">{elsewhere}</p>}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Annulla
          </Button>
          <Button onClick={submit} disabled={busy}>
            {busy ? 'Salvataggio…' : 'Salva'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/**
 * The «Note sulle clausole» section of a contract's page (REB-762): for each clause that
 * reads ambiguously, where it is, what it says word for word and how the person decided
 * to read it, so the reading stays next to the words it answers. A note is edited and
 * never deleted. A person who may not write sees the list and no buttons.
 */
export function ClauseNotesSection({ contractId }: { contractId: string }) {
  const canCreate = useCan('create_clause_note')
  const canUpdate = useCan('update_clause_note')
  const notes = useClauseNotes(contractId)
  const [dialog, setDialog] = useState<{ open: boolean; note?: ClauseNote }>({ open: false })

  return (
    <Card>
      <CardContent className="pt-6">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-semibold">Note sulle clausole</h2>
          {canCreate && (
            <Button size="sm" variant="outline" onClick={() => setDialog({ open: true })}>
              <Plus className="mr-2 size-4" />
              Aggiungi una nota
            </Button>
          )}
        </div>

        {notes.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : notes.isError ? (
          <QueryErrorBanner error={notes.error} />
        ) : (notes.data ?? []).length === 0 ? (
          <p className="text-muted-foreground">
            Nessuna nota: scrivi come hai letto una clausola ambigua, accanto alle sue parole.
          </p>
        ) : (
          <ul className="divide-y">
            {(notes.data ?? []).map((note) => (
              <li key={note.id} className="space-y-3 py-4 first:pt-0 last:pb-0">
                <div className="flex items-start justify-between gap-4">
                  <h3 className="font-medium">{note.riferimento_clausola}</h3>
                  {canUpdate && (
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={`Modifica la nota ${note.riferimento_clausola}`}
                      onClick={() => setDialog({ open: true, note })}
                    >
                      Modifica
                    </Button>
                  )}
                </div>
                <div>
                  <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Testo
                  </p>
                  <blockquote className="whitespace-pre-wrap rounded-md border-l-2 bg-muted px-3 py-2 text-sm text-muted-foreground">
                    {note.testo_letterale}
                  </blockquote>
                </div>
                <div>
                  <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Interpretazione adottata
                  </p>
                  <p className="whitespace-pre-wrap text-sm">{note.interpretazione_adottata}</p>
                </div>
                {note.note !== null && note.note !== '' && (
                  <div>
                    <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      Note
                    </p>
                    <p className="whitespace-pre-wrap text-sm">{note.note}</p>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>

      <ClauseNoteDialog
        open={dialog.open}
        onOpenChange={(open) => setDialog((previous) => ({ ...previous, open }))}
        contractId={contractId}
        note={dialog.note}
      />
    </Card>
  )
}
