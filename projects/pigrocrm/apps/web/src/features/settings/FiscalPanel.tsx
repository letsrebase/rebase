import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import {
  useFiscalProfile,
  useSaveFiscalProfile,
  type FiscalProfile,
} from '@/features/invoices/queries'

interface FiscalField {
  name:
    | 'aliquota_iva_default'
    | 'natura_default'
    | 'riferimento_normativo'
    | 'soglia_bollo'
    | 'importo_bollo'
    | 'condizioni_pagamento'
    | 'modalita_pagamento'
    | 'giorni_scadenza'
    | 'iban'
    | 'coefficiente_redditivita'
    | 'aliquota_imposta_sostitutiva'
    | 'aliquota_inps'
  label: string
  hint?: string
  /** Read by an Italian regime only: hidden, and sent empty, on «Estero» (REB-621). */
  italian?: boolean
}

/**
 * The regime choice (REB-621, spec 2026-10-03 §1.3): the two Italian codes the server
 * implements and the `non-it` pack, which has no FatturaPA code at all. A select and
 * not the free RF field this panel had: `resolve_regime` refuses every other code
 * anyway («non implementato»), so a free field only offered ways to be refused.
 */
type Regime = 'RF19' | 'RF01' | 'estero'
type PackId = 'it-flat-rate' | 'non-it'

const REGIMI: readonly { value: Regime; label: string }[] = [
  { value: 'RF19', label: 'Forfettario (RF19)' },
  { value: 'RF01', label: 'Ordinario (RF01)' },
  { value: 'estero', label: 'Estero: nessun regime italiano' },
]

function regimeOf(values: { pack_id: PackId; codice_regime: string }): Regime {
  if (values.pack_id === 'non-it') return 'estero'
  return values.codice_regime === 'RF01' ? 'RF01' : 'RF19'
}

/**
 * `FiscalProfileUpsert.riferimento_normativo`'s own default, repeated here because the
 * panel PUTs every key on every save and the server's default therefore never applies.
 * Seeding this blank -- as this form did until the income columns arrived -- meant a
 * first save stored an empty normative reference: harmless only by accident, because
 * `_Forfettario.resolve_line_vat` treats the empty string as falsy and falls back to
 * this same sentence at emission time. Better to show the owner the sentence their
 * invoices will actually carry, where they can read and amend it, than to store a blank
 * and rely on a fallback three layers away.
 */
const RIFERIMENTO_NORMATIVO_FORFETTARIO =
  "Operazione non soggetta a IVA ai sensi dell'art. 1, commi 54-89, " +
  'L. 190/2014 - regime forfettario'

/**
 * `applica_bollo` is a boolean and lives outside this list; everything else is a text
 * input because the values are codes and decimals the backend validates, and a
 * client-side guess at their shape would only produce a second, weaker validator.
 */
const FIELDS: readonly FiscalField[] = [
  { name: 'aliquota_iva_default', label: 'Aliquota IVA predefinita' },
  // Natura and riferimento stay on «Estero» too: a foreign company whose untaxed line
  // must carry a legal mention names it here, and the server requires a natura beside
  // a zero rate whoever issues (`invoice_lines`' own rule). Emptied when the regime
  // turns foreign, since «N2.2» is the forfettario's.
  { name: 'natura_default', label: 'Natura', hint: 'Obbligatoria quando l’aliquota è 0' },
  { name: 'riferimento_normativo', label: 'Riferimento normativo' },
  { name: 'soglia_bollo', label: 'Soglia bollo', italian: true },
  { name: 'importo_bollo', label: 'Importo bollo', italian: true },
  { name: 'condizioni_pagamento', label: 'Condizioni di pagamento', hint: 'TP02' },
  { name: 'modalita_pagamento', label: 'Modalità di pagamento', hint: 'MP05' },
  { name: 'giorni_scadenza', label: 'Giorni di scadenza' },
  { name: 'iban', label: 'IBAN' },
  // The three income-calculation columns. They decide nothing about an invoice -- they
  // are what the fiscal estimate divides the year's takings by -- but they live on the
  // same single row, so this is the only screen that can ever set them. Percentages,
  // the way their owner reads them off a commercialista's letter: `67`, not `0.67`.
  {
    name: 'coefficiente_redditivita',
    label: 'Coefficiente di redditività',
    hint: 'La quota dei ricavi che diventa reddito imponibile, secondo il codice ATECO',
    italian: true,
  },
  {
    name: 'aliquota_imposta_sostitutiva',
    label: 'Aliquota imposta sostitutiva',
    hint: 'Sostituisce IRPEF e addizionali: 5% nei primi cinque anni, poi 15%',
    italian: true,
  },
  {
    name: 'aliquota_inps',
    label: 'Aliquota INPS',
    hint: 'I contributi previdenziali calcolati sul reddito imponibile',
    italian: true,
  },
]

type Values = Record<FiscalField['name'], string> & {
  applica_bollo: boolean
  pack_id: PackId
  codice_regime: string
}

function emptyValues(): Values {
  return {
    pack_id: 'it-flat-rate',
    codice_regime: 'RF19',
    aliquota_iva_default: '0.00',
    natura_default: 'N2.2',
    riferimento_normativo: RIFERIMENTO_NORMATIVO_FORFETTARIO,
    soglia_bollo: '77.47',
    importo_bollo: '2.00',
    condizioni_pagamento: 'TP02',
    modalita_pagamento: 'MP05',
    giorni_scadenza: '30',
    iban: '',
    // `FiscalProfileUpsert`'s own defaults, and they have to be repeated here for the
    // same reason every other value on this list is: the save is a full replace, so a
    // key this form does not know about is not left alone -- it is written back as
    // whatever the form last held for it. Before these three existed here, pressing
    // Salva reset all of them to the server's defaults, which was invisible only
    // because nothing had ever changed them yet.
    coefficiente_redditivita: '67.00',
    aliquota_imposta_sostitutiva: '5.00',
    aliquota_inps: '26.07',
    applica_bollo: true,
  }
}

function valuesFrom(profile: FiscalProfile): Values {
  const values = emptyValues()
  for (const field of FIELDS) {
    const stored = profile[field.name]
    values[field.name] = stored === null || stored === undefined ? '' : String(stored)
  }
  values.applica_bollo = profile.applica_bollo
  values.pack_id = profile.pack_id === 'non-it' ? 'non-it' : 'it-flat-rate'
  values.codice_regime = profile.codice_regime ?? ''
  return values
}

/**
 * What a save sends. The same fifteen keys whichever the regime, because the PUT is a
 * full replace and a key left out is a key the server defaults; on «Estero» the ones an
 * Italian regime reads go explicitly empty, since the server's own defaults for them
 * are the forfettario's («N2.2», the bollo on, the ATECO coefficient) and a foreign
 * profile that carried them would be refused or, worse, would estimate Italian taxes.
 */
function bodyFrom(values: Values): Record<string, unknown> {
  const { pack_id, codice_regime, ...rest } = values
  if (pack_id === 'non-it') {
    return {
      ...rest,
      pack_id,
      codice_regime: null,
      natura_default: rest.natura_default || null,
      riferimento_normativo: rest.riferimento_normativo || null,
      applica_bollo: false,
      coefficiente_redditivita: null,
      aliquota_imposta_sostitutiva: null,
      aliquota_inps: null,
    }
  }
  return { ...rest, pack_id, codice_regime }
}

export function FiscalPanel({ aziendaId }: { aziendaId: string }) {
  const profile = useFiscalProfile(aziendaId)

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-medium">Fiscale</h2>
        <p className="text-muted-foreground text-sm">
          I parametri che decidono aliquota, natura e bollo su ogni riga di fattura. Una
          fattura emessa conserva una copia di questi valori, quindi cambiarli qui non
          tocca i documenti già emessi. Gli ultimi tre non finiscono in fattura: servono
          a stimare imposta sostitutiva e contributi sui compensi dell’anno.
        </p>
      </div>

      {profile.isError ? <QueryErrorBanner error={profile.error} /> : null}

      {!profile.isLoading && !profile.isError && profile.data === null ? (
        <p className="text-muted-foreground text-sm">
          Profilo non ancora configurato: senza di esso non è possibile emettere fatture.
        </p>
      ) : null}

      {profile.isLoading || profile.isError ? null : (
        // A failed read hides the form entirely -- `isError`, not just `isLoading`.
        // A 404 is not an error here (`useFiscalProfile` maps it to `null`, "not configured
        // yet"), so this branch only fires on a *real* failure: the row may well exist
        // and simply be unreadable. Rendering the blank form in that state invites
        // somebody to fill it in and press Salva, and the save is a PUT of every key --
        // it would overwrite a stored profile the panel was never able to show them.
        // Keyed on identity so the form seeds at mount rather than in an effect: one
        // render with the right values, and a later refetch cannot overwrite what the
        // user is typing.
        <FiscalForm
          // The azienda is part of the key: two aziende without a profile would
          // otherwise share one mounted form, and values typed for the first would be
          // saved under the second after a switch.
          key={`${aziendaId}:${profile.data?.id ?? 'nuovo'}`}
          aziendaId={aziendaId}
          profile={profile.data ?? null}
        />
      )}
    </div>
  )
}

function FiscalForm({ aziendaId, profile }: { aziendaId: string; profile: FiscalProfile | null }) {
  const save = useSaveFiscalProfile(aziendaId)
  const [values, setValues] = useState<Values>(() =>
    profile ? valuesFrom(profile) : emptyValues(),
  )
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  // What «Estero» empties, kept aside while the choice is unsaved: a person who looks
  // at the foreign form and comes back finds their natura, declaration and bollo
  // switch as they were, not the blanks a save would have written.
  const [kept, setKept] = useState<Pick<
    Values,
    'natura_default' | 'riferimento_normativo' | 'applica_bollo'
  > | null>(null)

  function submit() {
    setProblem(null)
    save.mutate(bodyFrom(values), {
      onSuccess: () => toast.success('Profilo fiscale salvato'),
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  // Reads `values` and calls the two setters outside any updater: an updater has to be
  // pure (React runs it twice in StrictMode), and the previous shape set `kept` from
  // inside one while reading `kept` from a stale closure.
  function pickRegime(regime: Regime) {
    if (regime === 'estero') {
      if (values.pack_id !== 'non-it') {
        setKept({
          natura_default: values.natura_default,
          riferimento_normativo: values.riferimento_normativo,
          applica_bollo: values.applica_bollo,
        })
      }
      // The forfettario's natura and declaration mean nothing abroad; the bollo is an
      // Italian duty. Emptied here so the foreign save never carries them.
      setValues({
        ...values,
        pack_id: 'non-it',
        codice_regime: '',
        natura_default: '',
        riferimento_normativo: '',
        applica_bollo: false,
      })
      return
    }
    const restored = values.pack_id === 'non-it' && kept ? kept : {}
    setValues({ ...values, ...restored, pack_id: 'it-flat-rate', codice_regime: regime })
  }

  const fieldError = problem ? fieldErrorFrom(problem) : null
  const regime = regimeOf(values)
  const abroad = regime === 'estero'
  const visible = FIELDS.filter((field) => !(abroad && field.italian))
  // A refusal that names a field this regime does not show (a bollo value sent from
  // hidden state) has nowhere to land: it goes to the banner, never nowhere.
  const rendered = new Set<string>([
    'codice_regime',
    ...visible.map((field) => field.name),
    ...(abroad ? [] : ['applica_bollo']),
  ])
  const bannered = problem && (!fieldError || !rendered.has(fieldError.field))

  return (
    <div className="space-y-4">
      {bannered ? <QueryErrorBanner error={problem} /> : null}

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
                : 'Il codice che la fattura elettronica dichiara allo SdI.'}
            </p>
          )}
        </div>
        {visible.map((field) => (
          <div key={field.name} className="space-y-2">
            <Label htmlFor={`fiscal-${field.name}`}>{field.label}</Label>
            <Input
              id={`fiscal-${field.name}`}
              value={values[field.name]}
              aria-invalid={fieldError?.field === field.name ? true : undefined}
              onChange={(event) =>
                setValues((previous) => ({ ...previous, [field.name]: event.target.value }))
              }
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

      {abroad ? null : (
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={values.applica_bollo}
            onChange={(event) =>
              setValues((previous) => ({ ...previous, applica_bollo: event.target.checked }))
            }
          />
          Applica il bollo virtuale sopra la soglia
        </label>
      )}

      <div className="flex justify-end">
        <Button onClick={submit} disabled={save.isPending}>
          Salva
        </Button>
      </div>
    </div>
  )
}
