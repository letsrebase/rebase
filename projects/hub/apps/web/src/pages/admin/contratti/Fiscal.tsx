import { useMutation } from '@tanstack/react-query'
import { useState, type ComponentProps, type FormEvent } from 'react'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { admin, ApiError, type Fiscal, type FiscalData } from '@/lib/api'
import {
  draftFromFiscal,
  fiscalLine,
  refillFiscal,
  toFiscalData,
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
  const [draft, setDraft] = useState<FiscalDraft>(() => draftFromFiscal(fiscale))
  // The fields typed in and not saved yet, and the saved record the form was last filled
  // from. A newer record (a save here or elsewhere, a refetch) refills the form while it
  // stays open, except in those fields, so a save never writes back a value older than
  // the record. Adjusted while rendering rather than in an effect: React's pattern for
  // state that follows a prop.
  const [typed, setTyped] = useState<ReadonlySet<FiscalKey>>(() => new Set())
  const version = fiscale?.updated_at ?? null
  const [filledFrom, setFilledFrom] = useState(version)
  if (version !== filledFrom) {
    setFilledFrom(version)
    setDraft(refillFiscal(draft, fiscale, typed))
  }
  function change(next: FiscalDraft) {
    setTyped(new Set([...typed, ...typedFiscalFields(draft, next)]))
    setDraft(next)
  }
  const [saved, setSaved] = useState(false)
  const save = useMutation({
    mutationFn: (data: FiscalData) => admin.saveFiscal(freelancerId, data),
    onSuccess: () => {
      // Saved: the record that comes back refills every field.
      setTyped(new Set())
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
