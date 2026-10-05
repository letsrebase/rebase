import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import { queryKeys } from '@/lib/query'
import { LegalEntityImages } from './LegalEntityImages'
import { useSaveLegalEntity, type LegalEntityRecord } from './queries'
import { StaleRowBanner } from './StaleRowBanner'
import { isStaleRow, type StalePhase } from './staleRow'

/**
 * Every field the upsert accepts, in the order a person reads an invoice header:
 * who you are, where you are, how to reach you, how you are taxed. `logo_key` and
 * `firma_key` are not fields: they are the server's storage keys, written by the two
 * uploads `LegalEntityImages` offers under this form (REB-629) and read-only on the row.
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
function valuesFrom(profile: LegalEntityRecord): Record<FieldName, string> {
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
export function LegalEntityPanel({ azienda }: { azienda: LegalEntityRecord }) {
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
          below, and sends the version it was seeded from with every save (REB-622), so
          a draft started before somebody else's save is refused rather than written. */}
      <LegalEntityForm key={`form-${azienda.id}`} profile={azienda} />
      {/* Keyed like the form: a refusal shown under azienda A's block must not
          outlive the switch to B. A distinct prefix on each, since the two are
          siblings: two siblings sharing one key is undefined to React, and in
          practice (REB-632, seen on the switch after «Nuova azienda») the previous
          azienda's form stayed mounted under the new one. */}
      <LegalEntityImages key={`images-${azienda.id}`} azienda={azienda} />
    </div>
  )
}

/** The reloaded row under the draft: a field the person typed in keeps the draft, every
 *  other one takes the row's value. What «Ricarica» shows, and what an untouched form
 *  follows on its own. */
function withDraft(
  fresh: Record<FieldName, string>,
  draft: Record<FieldName, string>,
  touched: ReadonlySet<FieldName>,
): Record<FieldName, string> {
  const merged = { ...fresh }
  for (const name of touched) merged[name] = draft[name]
  return merged
}

function LegalEntityForm({ profile }: { profile: LegalEntityRecord }) {
  const queryClient = useQueryClient()
  const save = useSaveLegalEntity(profile.id)
  // The row the inputs were built from. Its `updated_at` is the version every save
  // sends (REB-622, spec 2026-10-03 §11), and only a reseed moves it: a newer row that
  // arrives while a draft is open is not adopted, so a Salva over it carries the old
  // version and the server refuses it, instead of the draft's untouched fields quietly
  // replacing what somebody else just saved. State adjusted during render, the way
  // React documents for «adjusting state when a prop changes».
  const [seeded, setSeeded] = useState(profile)
  const [values, setValues] = useState<Record<FieldName, string>>(() => valuesFrom(profile))
  // The fields typed in since the last seed: what a reload keeps.
  const [touched, setTouched] = useState<ReadonlySet<FieldName>>(() => new Set())
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const [stale, setStale] = useState<StalePhase>('none')
  // Set by «Ricarica»: the next row that differs from the seeded one is adopted under
  // the draft even though the form is touched. It waits for that row rather than being
  // cleared by a render in between, since the refetch's answer and the promise that
  // follows it reach this component in no fixed order.
  const [adopt, setAdopt] = useState(false)
  const [reloading, setReloading] = useState(false)
  // The prop as last seen, so only a row that *arrived* is considered: after a save the
  // seed moves to the answer while the prop may still be the previous row for a render,
  // and comparing versions alone would adopt that older row back over the saved values.
  const [seen, setSeen] = useState(profile)

  // An untouched form follows a row as it arrives; «Ricarica» adopts whatever row is in
  // hand once it differs from the seed, seen before or not, since a background refetch
  // may already have brought the other admin's row under the draft and the reload then
  // fetches the same object again.
  const arrived = profile !== seen
  if (arrived) setSeen(profile)
  if (profile.updated_at !== seeded.updated_at && ((arrived && touched.size === 0) || adopt)) {
    setSeeded(profile)
    setValues(withDraft(valuesFrom(profile), values, touched))
    setAdopt(false)
  }

  function submit() {
    setProblem(null)
    save.mutate(
      { ...values, updated_at: seeded.updated_at },
      {
        // The answer is the row as saved, normalised: seeded from it at once, so a
        // second Salva before the list refetches already carries the new version.
        onSuccess: (saved) => {
          setSeeded(saved)
          setValues(valuesFrom(saved))
          setTouched(new Set())
          setStale('none')
          toast.success('Azienda salvata')
        },
        onError: (error) => {
          const refusal = toProblem(error)
          setProblem(refusal)
          if (isStaleRow(refusal)) setStale('refused')
        },
      },
    )
  }

  async function reload() {
    setReloading(true)
    try {
      // `refetchQueries` with `throwOnError`, not `invalidateQueries`: the latter
      // resolves on a failed refetch too, and the banner would read «ricaricata» over
      // fields nothing reloaded.
      await queryClient.refetchQueries({ queryKey: queryKeys.aziende }, { throwOnError: true })
    } catch (error) {
      toast.error(toProblem(error).detail)
      return
    } finally {
      setReloading(false)
    }
    setAdopt(true)
    setProblem(null)
    setStale('reloaded')
  }

  const fieldError = problem ? fieldErrorFrom(problem) : null

  return (
    <div className="space-y-4">
      {stale !== 'none' ? (
        <StaleRowBanner phase={stale} onReload={() => void reload()} reloading={reloading} />
      ) : null}
      {/* A refusal of another kind after a reload shows beside the reminder, never
          behind it: the reminder says what to do next, the refusal says why it did
          not work. */}
      {problem && !fieldError && !isStaleRow(problem) ? <QueryErrorBanner error={problem} /> : null}

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
                setTouched((previous) => new Set(previous).add(field.name))
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
