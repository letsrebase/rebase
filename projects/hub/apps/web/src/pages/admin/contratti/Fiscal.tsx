import { useMutation } from '@tanstack/react-query'
import { useState, type ComponentProps, type FormEvent } from 'react'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { admin, ApiError, type Fiscal, type FiscalData } from '@/lib/api'
import {
  draftFromFiscal,
  fiscalLine,
  newerFiscal,
  refillFiscal,
  toFiscalData,
  typedAfterSave,
  typedFiscalFields,
  type FiscalDraft,
  type FiscalKey,
} from '@/lib/contracts'

/** The four tax fields, shared by «Match e contratti» and «Chi e per chi» of «Crea match». */
export function FiscalFields({
  idPrefix,
  draft,
  onChange,
  wrong,
}: {
  idPrefix: string
  draft: FiscalDraft
  onChange: (draft: FiscalDraft) => void
  wrong: (field: string) => true | undefined
}) {
  const field = (name: keyof FiscalDraft, label: string, props: ComponentProps<typeof Input> = {}) => (
    <div className="space-y-1.5">
      <Label htmlFor={`${idPrefix}-${name}`}>{label}</Label>
      <Input
        id={`${idPrefix}-${name}`}
        value={draft[name]}
        onChange={(event) => onChange({ ...draft, [name]: event.target.value })}
        aria-invalid={wrong(name)}
        {...props}
      />
    </div>
  )
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {field('codice_fiscale', 'Codice fiscale', { required: true, maxLength: 40, autoComplete: 'off' })}
      {field('partita_iva', 'Partita IVA', { required: true, maxLength: 40, inputMode: 'numeric' })}
      {field('domicilio', 'Domicilio professionale', { required: true, maxLength: 300 })}
      {field('pec', 'PEC, se ce l’ha', { type: 'email', maxLength: 320 })}
    </div>
  )
}

/** «Dati fiscali»: set once and read rarely, so closed, its summary saying whether they
 *  are saved. */
export function FiscalSection({
  freelancerId,
  fiscale,
  onSaving,
  onSaved,
}: {
  freelancerId: string
  fiscale: Fiscal | null
  /** A save is an action like the others: the page drops its last sentence. */
  onSaving: () => void
  onSaved: () => void
}) {
  // The draft, with the fields typed in and not saved yet, and the saved record it was
  // last filled from. Only a newer record (a save here or elsewhere, a refetch) refills
  // the form while it stays open, and never those fields, so a save never writes back a
  // value older than the record. Adjusted while rendering rather than in an effect:
  // React's pattern for state that follows a prop. One state, so a save's success reads
  // the draft as it is then.
  const [form, setForm] = useState<{ draft: FiscalDraft; typed: ReadonlySet<FiscalKey>; held: Fiscal | null }>(
    () => ({ draft: draftFromFiscal(fiscale), typed: new Set(), held: fiscale }),
  )
  const { draft } = form
  if (newerFiscal(fiscale, form.held)) {
    setForm({ draft: refillFiscal(form.draft, fiscale, form.typed), typed: form.typed, held: fiscale })
  }
  function change(next: FiscalDraft) {
    setForm((current) => ({
      ...current,
      draft: next,
      typed: new Set([...current.typed, ...typedFiscalFields(current.draft, next)]),
    }))
  }
  const [saved, setSaved] = useState(false)
  const save = useMutation({
    mutationFn: (data: FiscalData) => admin.saveFiscal(freelancerId, data),
    onSuccess: (record, sent) => {
      // The fields saved as they read now take the record the save answered, at once, or
      // the newer one the form already holds when the answer is older; one typed in again
      // while the save was on its way keeps its text. From here only a record newer than
      // the one held refills the form.
      setForm((current) => {
        const typed = typedAfterSave(current.draft, current.typed, sent)
        const held = newerFiscal(record, current.held) ? record : current.held
        return { draft: refillFiscal(current.draft, held, typed), typed, held }
      })
      setSaved(true)
      onSaved()
    },
  })
  const failure = save.error instanceof ApiError ? save.error : null
  function submit(event: FormEvent) {
    event.preventDefault()
    setSaved(false)
    onSaving()
    save.mutate(toFiscalData(draft))
  }
  return (
    <details className="border border-border bg-card">
      <summary className="cursor-pointer px-4 py-3 text-sm">
        <span className="font-medium">Dati fiscali</span>{' '}
        <span className="ml-2 text-muted-foreground">{fiscale ? fiscalLine(fiscale) : 'Mancano'}</span>
      </summary>
      <form onSubmit={submit} className="space-y-3 border-t px-4 py-4">
        <FiscalFields
          idPrefix="contratti"
          draft={draft}
          onChange={change}
          wrong={(field) => failure?.fields.includes(field) || undefined}
        />
        <Button type="submit" size="sm" disabled={save.isPending}>
          {save.isPending ? 'Salvo…' : 'Salva i dati fiscali'}
        </Button>
        {saved && (
          <p role="status" className="text-sm text-muted-foreground">
            Dati fiscali salvati.
          </p>
        )}
        {save.error && (
          <p role="alert" className="text-sm text-destructive">
            {failure ? failure.message : 'Non riesco a salvare i dati fiscali.'}
          </p>
        )}
      </form>
    </details>
  )
}
