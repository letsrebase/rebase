import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import { useEmitter, useSaveEmitter, type EmitterRecord } from './queries'

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
function valuesFrom(profile: EmitterRecord): Record<FieldName, string> {
  const values = emptyValues()
  for (const field of FIELDS) {
    const stored = profile[field.name]
    values[field.name] = stored ?? ''
  }
  return values
}

export function EmitterPanel() {
  const emitter = useEmitter()

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-medium">Emittente</h2>
        <p className="text-muted-foreground text-sm">
          Chi emette i documenti. Questi dati compaiono nell'intestazione dei PDF e sono la base su
          cui verrà costruita la fattura elettronica, quindi vale la pena che siano esatti.
        </p>
      </div>

      {emitter.isError ? <QueryErrorBanner error={emitter.error} /> : null}

      {!emitter.isLoading && !emitter.isError && emitter.data === null ? (
        <p className="text-muted-foreground text-sm">
          Profilo non ancora configurato: compila i campi e salva.
        </p>
      ) : null}

      {emitter.isLoading || emitter.isError ? null : (
        // A failed read hides the form entirely -- `isError`, not just `isLoading`.
        // A 404 is not an error here (`useEmitter` maps it to `null`, "not configured
        // yet"), so this branch only fires on a *real* failure: the row may well exist
        // and simply be unreadable. Rendering the blank form in that state invites
        // somebody to fill it in and press Salva, and the save is a PUT of every key --
        // it would overwrite a stored profile the panel was never able to show them.
        // Keyed on the profile's identity so the form seeds its state at mount
        // instead of in an effect. Seeding in an effect meant one render with the
        // wrong values and a cascading re-render; keying makes "seed once" a
        // property of mounting rather than a flag the component has to maintain,
        // and a later refetch cannot overwrite what the user is typing because the
        // key does not change.
        <EmitterForm key={emitter.data?.id ?? 'nuovo'} profile={emitter.data ?? null} />
      )}
    </div>
  )
}

function EmitterForm({ profile }: { profile: EmitterRecord | null }) {
  const save = useSaveEmitter()
  const [values, setValues] = useState<Record<FieldName, string>>(() =>
    profile ? valuesFrom(profile) : emptyValues(),
  )
  const [problem, setProblem] = useState<ProblemDetail | null>(null)

  function submit() {
    setProblem(null)
    save.mutate(values, {
      onSuccess: () => toast.success('Profilo emittente salvato'),
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
              aria-invalid={fieldError?.field === field.name ? true : undefined}
              onChange={(event) =>
                setValues((previous) => ({ ...previous, [field.name]: event.target.value }))
              }
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
