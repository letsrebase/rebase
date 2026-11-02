import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import { queryKeys } from '@/lib/query'
import { useSaveFiscalProfile, type FiscalProfile } from '@/features/invoices/queries'
import {
  FIELDS,
  ORDINARY_RATE,
  PACK_OF,
  REGIMI,
  bodyFrom,
  emptyValues,
  regimeOf,
  valuesFrom,
  withDraft,
  type FiscalField,
  type Regime,
  type ValueName,
  type Values,
} from './fiscalModel'
import { StaleRowBanner } from './StaleRowBanner'
import { isStaleRow, type StalePhase } from './staleRow'

export function FiscalForm({ aziendaId, profile }: { aziendaId: string; profile: FiscalProfile | null }) {
  const queryClient = useQueryClient()
  const save = useSaveFiscalProfile(aziendaId)
  // The row the inputs were built from, `null` for a profile not saved yet. Its
  // `updated_at` is the version every save sends (REB-622, spec 2026-10-03 §11), and
  // only a reseed moves it: a newer row that arrives while a draft is open is not
  // adopted, so a Salva over it is refused by the server instead of the draft's
  // untouched fields replacing what somebody else just saved. State adjusted during
  // render, as `LegalEntityForm` does.
  const [seeded, setSeeded] = useState(profile)
  const [values, setValues] = useState<Values>(() =>
    profile ? valuesFrom(profile) : emptyValues(),
  )
  const [touched, setTouched] = useState<ReadonlySet<ValueName>>(() => new Set())
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const [stale, setStale] = useState<StalePhase>('none')
  const [adopt, setAdopt] = useState(false)
  const [reloading, setReloading] = useState(false)
  // What leaving the forfettario empties, kept aside while the choice is unsaved: a
  // person who looks at another regime and comes back finds their natura, declaration
  // and bollo switch as they were, not the blanks a save would have written.
  const [kept, setKept] = useState<Pick<
    Values,
    'natura_default' | 'riferimento_normativo' | 'applica_bollo'
  > | null>(null)

  // As in `LegalEntityForm`: an untouched form follows a row as it arrives, and
  // «Ricarica» adopts the row in hand once it differs from the seed.
  const [seen, setSeen] = useState(profile)
  const arrived = profile !== seen
  if (arrived) setSeen(profile)
  if (
    (profile?.updated_at ?? null) !== (seeded?.updated_at ?? null) &&
    ((arrived && touched.size === 0) || adopt)
  ) {
    setSeeded(profile)
    setValues(withDraft(profile ? valuesFrom(profile) : emptyValues(), values, touched))
    setAdopt(false)
  }

  function change(patch: Partial<Values>) {
    setTouched((previous) => {
      const next = new Set(previous)
      for (const name of Object.keys(patch) as ValueName[]) next.add(name)
      return next
    })
    setValues((previous) => ({ ...previous, ...patch }))
  }

  function submit() {
    setProblem(null)
    // `null` on a first save: the version of a profile not saved yet, which the server
    // accepts while the row is still missing and refuses once somebody else created it.
    save.mutate({ ...bodyFrom(values), updated_at: seeded?.updated_at ?? null }, {
      onSuccess: (saved) => {
        setSeeded(saved)
        setValues(valuesFrom(saved))
        setTouched(new Set())
        setStale('none')
        toast.success('Profilo fiscale salvato')
      },
      onError: (error) => {
        const refusal = toProblem(error)
        setProblem(refusal)
        if (isStaleRow(refusal)) setStale('refused')
      },
    })
  }

  async function reload() {
    setReloading(true)
    try {
      await queryClient.refetchQueries(
        { queryKey: queryKeys.fiscalProfile(aziendaId) },
        { throwOnError: true },
      )
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

  // Reads `values` and calls the two setters outside any updater: an updater has to be
  // pure (React runs it twice in StrictMode), and the previous shape set `kept` from
  // inside one while reading `kept` from a stale closure.
  function pickRegime(regime: Regime) {
    const leaving = regimeOf(values)
    if (regime === leaving) return
    if (leaving === 'RF19') {
      setKept({
        natura_default: values.natura_default,
        riferimento_normativo: values.riferimento_normativo,
        applica_bollo: values.applica_bollo,
      })
    }
    if (regime === 'estero') {
      // The forfettario's natura and declaration mean nothing abroad; the bollo is an
      // Italian duty. Emptied here so the foreign save never carries them.
      change({
        pack_id: 'non-it',
        codice_regime: '',
        natura_default: '',
        riferimento_normativo: '',
        applica_bollo: false,
      })
      return
    }
    if (regime === 'RF01') {
      // The ordinary regime taxes every line: no natura, no declaration, no bollo, and a
      // zero rate is refused, so the forfettario's 0.00 becomes the standard 22.00.
      const zeroRate = !/[1-9]/.test(values.aliquota_iva_default)
      change({
        pack_id: 'it-standard',
        codice_regime: 'RF01',
        natura_default: '',
        riferimento_normativo: '',
        applica_bollo: false,
        ...(zeroRate ? { aliquota_iva_default: ORDINARY_RATE } : {}),
      })
      return
    }
    // Back to the forfettario: what it kept, or its own defaults, and the zero rate it
    // alone allows. The income parameters a profile on another pack has not got come
    // back as the server's defaults, to read and amend.
    const defaults = emptyValues()
    const restored = kept ?? {
      natura_default: defaults.natura_default,
      riferimento_normativo: defaults.riferimento_normativo,
      applica_bollo: defaults.applica_bollo,
    }
    const blank = (name: FiscalField['name']) => values[name].trim() === ''
    change({
      ...restored,
      pack_id: PACK_OF.RF19,
      codice_regime: 'RF19',
      aliquota_iva_default: '0.00',
      ...(blank('soglia_bollo') ? { soglia_bollo: defaults.soglia_bollo } : {}),
      ...(blank('importo_bollo') ? { importo_bollo: defaults.importo_bollo } : {}),
      ...(blank('coefficiente_redditivita')
        ? { coefficiente_redditivita: defaults.coefficiente_redditivita }
        : {}),
      ...(blank('aliquota_imposta_sostitutiva')
        ? { aliquota_imposta_sostitutiva: defaults.aliquota_imposta_sostitutiva }
        : {}),
      ...(blank('aliquota_inps') ? { aliquota_inps: defaults.aliquota_inps } : {}),
    })
  }

  const fieldError = problem ? fieldErrorFrom(problem) : null
  const regime = regimeOf(values)
  const abroad = regime === 'estero'
  const forfettario = regime === 'RF19'
  const visible = FIELDS.filter((field) => field.shownFor?.includes(regime) ?? true)
  // A refusal that names a field this regime does not show (a bollo value sent from
  // hidden state) has nowhere to land: it goes to the banner, never nowhere.
  const rendered = new Set<string>([
    'codice_regime',
    ...visible.map((field) => field.name),
    ...(forfettario ? ['applica_bollo'] : []),
  ])
  const bannered = problem && (!fieldError || !rendered.has(fieldError.field))

  return (
    <div className="space-y-4">
      {stale !== 'none' ? (
        <StaleRowBanner phase={stale} onReload={() => void reload()} reloading={reloading} />
      ) : null}
      {bannered && !isStaleRow(problem) ? <QueryErrorBanner error={problem} /> : null}

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          {/* «Regime», not «Regime fiscale»: the azienda panel above already has a free
              field of that name, the caption the PDF header prints, and two fields with
              one label on one page is a question nobody should have to ask. */}
          <Label htmlFor="fiscal-regime">Regime</Label>
          <select
            id="fiscal-regime"
            className="border-input bg-background h-9 w-full rounded-md border px-3 text-sm"
            value={regime}
            // Locked while a save is in flight, like the azienda's fields: an edit made
            // between the PUT and its answer would be replaced by the row that comes back.
            disabled={save.isPending}
            aria-invalid={fieldError?.field === 'codice_regime' ? true : undefined}
            onChange={(event) => pickRegime(event.target.value as Regime)}
          >
            {REGIMI.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          {fieldError?.field === 'codice_regime' ? (
            <p className="text-destructive text-sm">{fieldError.message}</p>
          ) : (
            <p className="text-muted-foreground text-xs">
              {abroad
                ? 'Un’azienda con sede all’estero: niente fattura elettronica, niente bollo.'
                : forfettario
                  ? 'Il codice che la fattura elettronica dichiara allo SdI.'
                  : 'IVA su ogni riga, nessuna soglia del forfettario, nessun bollo.'}
            </p>
          )}
        </div>
        {visible.map((field) => (
          <div key={field.name} className="space-y-2">
            <Label htmlFor={`fiscal-${field.name}`}>{field.label}</Label>
            <Input
              id={`fiscal-${field.name}`}
              value={values[field.name]}
              disabled={save.isPending}
              aria-invalid={fieldError?.field === field.name ? true : undefined}
              onChange={(event) => change({ [field.name]: event.target.value })}
            />
            {fieldError?.field === field.name ? (
              <p className="text-destructive text-sm">{fieldError.message}</p>
            ) : abroad && field.name === 'aliquota_iva_default' ? (
              <p className="text-muted-foreground text-xs">
                L’aliquota del paese in cui l’azienda ha sede.
              </p>
            ) : abroad && field.name === 'natura_default' ? (
              <p className="text-muted-foreground text-xs">
                Solo se una riga ad aliquota zero deve portare una dicitura; altrimenti vuota.
              </p>
            ) : field.hint ? (
              <p className="text-muted-foreground text-xs">{field.hint}</p>
            ) : null}
          </div>
        ))}
      </div>

      {forfettario ? (
        <label className="flex items-center gap-2 text-sm">
          <Checkbox
            checked={values.applica_bollo}
            disabled={save.isPending}
            onCheckedChange={(checked) => change({ applica_bollo: checked === true })}
          />
          Applica il bollo virtuale sopra la soglia
        </label>
      ) : null}

      <div className="flex justify-end">
        <Button onClick={submit} disabled={save.isPending}>
          Salva
        </Button>
      </div>
    </div>
  )
}
