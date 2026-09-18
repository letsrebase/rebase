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
    | 'codice_regime'
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
  { name: 'codice_regime', label: 'Regime fiscale', hint: 'RF19 = forfettario' },
  { name: 'aliquota_iva_default', label: 'Aliquota IVA predefinita' },
  { name: 'natura_default', label: 'Natura', hint: 'Obbligatoria quando l’aliquota è 0' },
  { name: 'riferimento_normativo', label: 'Riferimento normativo' },
  { name: 'soglia_bollo', label: 'Soglia bollo' },
  { name: 'importo_bollo', label: 'Importo bollo' },
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
  },
  {
    name: 'aliquota_imposta_sostitutiva',
    label: 'Aliquota imposta sostitutiva',
    hint: 'Sostituisce IRPEF e addizionali: 5% nei primi cinque anni, poi 15%',
  },
  {
    name: 'aliquota_inps',
    label: 'Aliquota INPS',
    hint: 'I contributi previdenziali calcolati sul reddito imponibile',
  },
]

type Values = Record<FiscalField['name'], string> & { applica_bollo: boolean }

function emptyValues(): Values {
  return {
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
  return values
}

export function FiscalPanel() {
  const profile = useFiscalProfile()

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
        <FiscalForm key={profile.data?.id ?? 'nuovo'} profile={profile.data ?? null} />
      )}
    </div>
  )
}

function FiscalForm({ profile }: { profile: FiscalProfile | null }) {
  const save = useSaveFiscalProfile()
  const [values, setValues] = useState<Values>(() =>
    profile ? valuesFrom(profile) : emptyValues(),
  )
  const [problem, setProblem] = useState<ProblemDetail | null>(null)

  function submit() {
    setProblem(null)
    save.mutate(values, {
      onSuccess: () => toast.success('Profilo fiscale salvato'),
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
            ) : field.hint ? (
              <p className="text-muted-foreground text-xs">{field.hint}</p>
            ) : null}
          </div>
        ))}
      </div>

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

      <div className="flex justify-end">
        <Button onClick={submit} disabled={save.isPending}>
          Salva
        </Button>
      </div>
    </div>
  )
}
