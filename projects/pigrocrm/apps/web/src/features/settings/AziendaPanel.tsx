import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import { useSaveAzienda, type AziendaRecord } from './queries'

/**
 * Every field the upsert accepts, in the order a person reads an invoice header:
 * who you are, where you are, how to reach you, how you are taxed. `logo_key` and
 * `firma_key` are deliberately absent -- they are storage keys for images, and no
 * upload UI exists yet, so offering a raw key field would invite someone to type a
 * path that resolves to nothing. The images currently ship as build assets; the
 * slice's own notes record this as the one known gap.
 */
interface EmitterField {
  name:
    | 'nome'
    | 'ragione_sociale'
    | 'partita_iva'
    | 'codice_fiscale'
    | 'indirizzo'
    | 'cap'
    | 'comune'
    | 'provincia'
    | 'nazione'
    | 'pec'
    | 'codice_sdi'
    | 'telefono'
    | 'email'
    | 'sito_web'
    | 'regime_fiscale'
  label: string
  required?: boolean
  hint?: string
}

const FIELDS: readonly EmitterField[] = [
  { name: 'nome', label: 'Nome breve', hint: 'Come compare nelle liste; vuoto, è la ragione sociale' },
  { name: 'ragione_sociale', label: 'Ragione sociale', required: true },
  { name: 'partita_iva', label: 'Partita IVA', hint: '11 cifre' },
  { name: 'codice_fiscale', label: 'Codice fiscale' },
  { name: 'indirizzo', label: 'Indirizzo' },
  { name: 'cap', label: 'CAP' },
  { name: 'comune', label: 'Comune' },
  { name: 'provincia', label: 'Provincia' },
  { name: 'nazione', label: 'Nazione' },
  { name: 'pec', label: 'PEC' },
  { name: 'codice_sdi', label: 'Codice SDI', hint: '7 caratteri' },
  { name: 'telefono', label: 'Telefono' },
  { name: 'email', label: 'Email' },
  { name: 'sito_web', label: 'Sito web' },
  { name: 'regime_fiscale', label: 'Regime fiscale' },
]

type FieldName = EmitterField['name']

/** A never-configured profile still needs `nazione` to read `IT`, matching the
 *  server's own default, so the field is not blank on a first visit. */
function emptyValues(): Record<FieldName, string> {
  const values = Object.fromEntries(FIELDS.map((field) => [field.name, ''])) as Record<
    FieldName,
    string
  >
  return { ...values, nazione: 'IT' }
}

/** `null` and `undefined` both become `''`, because an input's value cannot be
 *  null -- but a stored empty string stays an empty string, and clearing a field
 *  sends `''` back rather than omitting the key. Omitting it would leave the old
 *  value in place while looking, on screen, as though it had been cleared. */
function valuesFrom(profile: AziendaRecord): Record<FieldName, string> {
  const values = emptyValues()
  for (const field of FIELDS) {
    const stored = profile[field.name]
    values[field.name] = stored ?? ''
  }
  return values
}

/**
 * One azienda's identity: who issues, where it is, how to reach it, how it is taxed
 * in words. The row always exists (a space is born with its default azienda, REB-615),
 * so there is no «not configured yet» state any more: a fresh space shows the name it
 * signed up with and empty fiscal fields, which is the state to fill in.
 */
export function AziendaPanel({ azienda }: { azienda: AziendaRecord }) {
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-medium">Azienda</h2>
        <p className="text-muted-foreground text-sm">
          Chi emette i documenti. Questi dati compaiono nell'intestazione dei PDF e sono la base su
          cui verrà costruita la fattura elettronica, quindi vale la pena che siano esatti.
        </p>
      </div>

      {/* Keyed on the row's identity alone so the form seeds its state at mount instead
          of in an effect: seeding in an effect meant one render with the wrong values
          and a cascading re-render. Not on `updated_at`: a background refetch after
          somebody else's save would remount the form and throw away what the person
          here is typing. The form itself follows a newer row while it is untouched,
          below, so a stale value is never what a Salva writes back by default. */}
      <AziendaForm key={azienda.id} profile={azienda} />
    </div>
  )
}

function AziendaForm({ profile }: { profile: AziendaRecord }) {
  const save = useSaveAzienda(profile.id)
  const [values, setValues] = useState<Record<FieldName, string>>(() => valuesFrom(profile))
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  // The row the form was last seeded from, and whether the person has typed since.
  // A background refetch that brings a newer row (another admin saved) reseeds an
  // untouched form, so Salva never writes back values that are already stale; one
  // with a draft in it keeps the draft, as the key comment above says. State derived
  // during render, the way React documents for «adjusting state when a prop changes».
  const [seededFrom, setSeededFrom] = useState(profile.updated_at)
  const [dirty, setDirty] = useState(false)
  if (profile.updated_at !== seededFrom) {
    setSeededFrom(profile.updated_at)
    if (!dirty) setValues(valuesFrom(profile))
  }

  function submit() {
    setProblem(null)
    save.mutate(values, {
      onSuccess: () => {
        setDirty(false)
        toast.success('Azienda salvata')
      },
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  const fieldError = problem ? fieldErrorFrom(problem) : null

  return (
    <div className="space-y-4">
      {problem && !fieldError ? <QueryErrorBanner error={problem} /> : null}

      <div className="grid gap-4 sm:grid-cols-2">
        {FIELDS.map((field) => (
          <div key={field.name} className="space-y-2">
            <Label htmlFor={`emitter-${field.name}`}>
              {field.label}
              {field.required ? ' *' : ''}
            </Label>
            <Input
              id={`emitter-${field.name}`}
              value={values[field.name]}
              // Locked while a save is in flight: a keystroke landing between the PUT
              // and its refetch would be marked clean by the success and overwritten
              // by the row that comes back. The lock lasts one round trip.
              disabled={save.isPending}
              aria-invalid={fieldError?.field === field.name ? true : undefined}
              onChange={(event) => {
                setDirty(true)
                setValues((previous) => ({ ...previous, [field.name]: event.target.value }))
              }}
            />
            {fieldError?.field === field.name ? (
              <p className="text-destructive text-sm">{fieldError.message}</p>
            ) : field.hint ? (
              <p className="text-muted-foreground text-xs">{field.hint}</p>
            ) : null}
          </div>
        ))}
      </div>

      <div className="flex justify-end">
        <Button onClick={submit} disabled={save.isPending}>
          Salva
        </Button>
      </div>
    </div>
  )
}
