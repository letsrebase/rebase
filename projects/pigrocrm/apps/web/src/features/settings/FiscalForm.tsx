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
  CHOICES,
  FIELDS,
  ORDINARY_RATE,
  PACK_OF,
  Regimes,
  bodyFrom,
  emptyValues,
  regimeOf,
  valuesFrom,
  withDraft,
  type ChoiceField,
  type FiscalField,
  type Regime,
  type ValueName,
  type Values,
} from './fiscalModel'
import { HouseholdFields } from './HouseholdFields'
import { StaleRowBanner } from './StaleRowBanner'
import { isStaleRow, type StalePhase } from './staleRow'

export function FiscalForm({ legalEntityId, profile }: { legalEntityId: string; profile: FiscalProfile | null }) {
  const queryClient = useQueryClient()
  const save = useSaveFiscalProfile(legalEntityId)
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
  // What leaving the flat-rate regime empties, kept aside while the choice is unsaved: a
  // person who looks at another regime and comes back finds their VAT nature, declaration
  // and stamp duty switch as they were, not the blanks a save would have written.
  const [kept, setKept] = useState<Pick<
    Values,
    'default_vat_nature' | 'legal_reference' | 'apply_stamp_duty'
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
        { queryKey: queryKeys.fiscalProfile(legalEntityId) },
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

  // A group picked proposes its coefficient in the typed field, which stays editable: the
  // estimate reads the typed value, and the group is the activity it was taken from.
  function pickChoice(choice: ChoiceField, value: string) {
    const picked =
      choice.name === 'ateco_group'
        ? profile?.flat_rate_groups?.find((candidate) => candidate.key === value)
        : undefined
    change({
      [choice.name]: value,
      ...(picked ? { profitability_coefficient: String(picked.coefficient) } : {}),
    })
  }

  // Reads `values` and calls the two setters outside any updater: an updater has to be
  // pure (React runs it twice in StrictMode), and the previous shape set `kept` from
  // inside one while reading `kept` from a stale closure.
  function pickRegime(regime: Regime) {
    const leaving = regimeOf(values)
    if (regime === leaving) return
    if (leaving === 'RF19') {
      setKept({
        default_vat_nature: values.default_vat_nature,
        legal_reference: values.legal_reference,
        apply_stamp_duty: values.apply_stamp_duty,
      })
    }
    if (regime === 'foreign') {
      // The flat-rate regime's VAT nature and declaration mean nothing abroad; the stamp duty is an
      // Italian duty. Emptied here so the foreign save never carries them.
      change({
        pack_id: 'non-it',
        regime_code: '',
        default_vat_nature: '',
        legal_reference: '',
        apply_stamp_duty: false,
      })
      return
    }
    if (regime === 'RF01' || regime === 'company') {
      // The ordinary regime, an srl's too, taxes every line: no VAT nature, no declaration, no stamp duty, and a
      // zero rate is refused, so the flat-rate regime's 0.00 becomes the standard 22.00.
      const zeroRate = !/[1-9]/.test(values.default_vat_rate)
      change({
        pack_id: PACK_OF[regime],
        regime_code: 'RF01',
        default_vat_nature: '',
        legal_reference: '',
        apply_stamp_duty: false,
        ...(zeroRate ? { default_vat_rate: ORDINARY_RATE } : {}),
      })
      return
    }
    // Back to the forfettario: what it kept, or its own defaults, and the zero rate it
    // alone allows. The income parameters a profile on another pack has not got come
    // back as the server's defaults, to read and amend.
    const defaults = emptyValues()
    const restored = kept ?? {
      default_vat_nature: defaults.default_vat_nature,
      legal_reference: defaults.legal_reference,
      apply_stamp_duty: defaults.apply_stamp_duty,
    }
    const blank = (name: FiscalField['name']) => values[name].trim() === ''
    change({
      ...restored,
      pack_id: PACK_OF.RF19,
      regime_code: 'RF19',
      default_vat_rate: '0.00',
      ...(blank('stamp_duty_threshold') ? { stamp_duty_threshold: defaults.stamp_duty_threshold } : {}),
      ...(blank('stamp_duty_amount') ? { stamp_duty_amount: defaults.stamp_duty_amount } : {}),
      ...(blank('profitability_coefficient')
        ? { profitability_coefficient: defaults.profitability_coefficient }
        : {}),
      ...(blank('substitute_tax_rate')
        ? { substitute_tax_rate: defaults.substitute_tax_rate }
        : {}),
      ...(blank('social_security_rate') ? { social_security_rate: defaults.social_security_rate } : {}),
    })
  }

  const fieldError = problem ? fieldErrorFrom(problem) : null
  const regime = regimeOf(values)
  const abroad = regime === 'foreign'
  const flat_rate = regime === 'RF19'
  const visible = FIELDS.filter(
    (field) => (field.shownFor?.includes(regime) ?? true) && (field.when?.(values) ?? true),
  )
  // The fields with an input of their own kind belong to the taxpayer's facts below.
  const plainFields = visible.filter((field) => field.input === undefined)
  const factFields = visible.filter((field) => field.input !== undefined)
  const choices = CHOICES.filter(
    (choice) => choice.shownFor.includes(regime) && (choice.when?.(values) ?? true),
  )
  const groups = profile?.flat_rate_groups ?? []
  const group = groups.find((candidate) => candidate.key === values.ateco_group)
  // A refusal that names a field this regime does not show (a bollo value sent from
  // hidden state) has nowhere to land: it goes to the banner, never nowhere.
  const rendered = new Set<string>([
    'regime_code',
    ...visible.map((field) => field.name),
    ...choices.map((choice) => choice.name),
    ...(flat_rate ? ['apply_stamp_duty'] : []),
  ])
  // The household's refusals name `household` or a path under it (a member's field).
  const householdShown = regime === 'RF01'
  const householdError =
    householdShown && fieldError?.field.split(/[.[]/)[0] === 'household' ? fieldError.message : null
  const bannered =
    problem && (!fieldError || (!rendered.has(fieldError.field) && !householdError))

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
            aria-invalid={fieldError?.field === 'regime_code' ? true : undefined}
            onChange={(event) => pickRegime(event.target.value as Regime)}
          >
            {Regimes.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          {fieldError?.field === 'regime_code' ? (
            <p className="text-destructive text-sm">{fieldError.message}</p>
          ) : (
            <p className="text-muted-foreground text-xs">
              {abroad
                ? 'Un’azienda con sede all’estero: niente fattura elettronica, niente bollo.'
                : flat_rate
                  ? 'Il codice che la fattura elettronica dichiara allo SdI.'
                  : 'IVA su ogni riga, nessuna soglia del forfettario, nessun bollo.'}
            </p>
          )}
        </div>
        {plainFields.map((field) => (
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
            ) : abroad && field.name === 'default_vat_rate' ? (
              <p className="text-muted-foreground text-xs">
                L’aliquota del paese in cui l’azienda ha sede.
              </p>
            ) : abroad && field.name === 'default_vat_nature' ? (
              <p className="text-muted-foreground text-xs">
                Solo se una riga ad aliquota zero deve portare una dicitura; altrimenti vuota.
              </p>
            ) : field.hint ? (
              <p className="text-muted-foreground text-xs">{field.hint}</p>
            ) : null}
          </div>
        ))}
      </div>

      {choices.length > 0 ? (
        <fieldset className="space-y-3">
          <legend className="text-sm font-medium">Parametri del contribuente</legend>
          <p className="text-muted-foreground text-xs">
            Le tue risposte, non quelle della legge: finché un parametro non è indicato, la cifra
            che lo richiede resta «non calcolabile».
          </p>
          <div className="grid gap-4 sm:grid-cols-2">
            {choices.map((choice) => (
              <div key={choice.name} className="space-y-2">
                <Label htmlFor={`fiscal-${choice.name}`}>{choice.label}</Label>
                <select
                  id={`fiscal-${choice.name}`}
                  className="border-input bg-background h-9 w-full rounded-md border px-3 text-sm"
                  value={values[choice.name]}
                  disabled={save.isPending}
                  aria-invalid={fieldError?.field === choice.name ? true : undefined}
                  onChange={(event) => pickChoice(choice, event.target.value)}
                >
                  {optionsOf(choice, groups).map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                {fieldError?.field === choice.name ? (
                  <p className="text-destructive text-sm">{fieldError.message}</p>
                ) : choice.name === 'ateco_group' && group ? (
                  <p className="text-muted-foreground text-xs">
                    Coefficiente di redditività dell’allegato 4: {Number(group.coefficient)}%. Codici
                    ATECO 2007: {group.ateco_2007.join(', ')}.
                  </p>
                ) : choice.name === 'ateco_group' && groups.length === 0 ? (
                  <p className="text-muted-foreground text-xs">
                    L’elenco dei gruppi compare dopo il primo salvataggio del profilo.
                  </p>
                ) : (
                  <p className="text-muted-foreground text-xs">{choice.hint}</p>
                )}
              </div>
            ))}
            {factFields.map((field) => (
              <div key={field.name} className="space-y-2">
                <Label htmlFor={`fiscal-${field.name}`}>{field.label}</Label>
                <Input
                  id={`fiscal-${field.name}`}
                  type={field.input}
                  value={values[field.name]}
                  disabled={save.isPending}
                  aria-invalid={fieldError?.field === field.name ? true : undefined}
                  onChange={(event) => change({ [field.name]: event.target.value })}
                />
                {fieldError?.field === field.name ? (
                  <p className="text-destructive text-sm">{fieldError.message}</p>
                ) : field.hint ? (
                  <p className="text-muted-foreground text-xs">{field.hint}</p>
                ) : null}
              </div>
            ))}
          </div>
        </fieldset>
      ) : null}

      {householdShown ? (
        <HouseholdFields
          household={values.household}
          disabled={save.isPending}
          error={householdError}
          onChange={(household) => change({ household })}
        />
      ) : null}

      {flat_rate ? (
        <label className="flex items-center gap-2 text-sm">
          <Checkbox
            checked={values.apply_stamp_duty}
            disabled={save.isPending}
            onCheckedChange={(checked) => change({ apply_stamp_duty: checked === true })}
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

function optionsOf(
  choice: ChoiceField,
  groups: readonly { key: string; number: number; name: string }[],
): readonly { value: string; label: string }[] {
  if (choice.options) return choice.options
  return [
    { value: '', label: 'Non indicato' },
    ...groups.map((group) => ({ value: group.key, label: `${group.number}. ${group.name}` })),
  ]
}
