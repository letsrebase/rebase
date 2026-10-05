import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import { useCreateAzienda, type AziendaRecord } from './queries'

/**
 * The fields a second azienda is opened with (REB-632, spec 2026-10-03 §3, §5): the
 * identity an invoice header prints, in the order `AziendaPanel` reads it, with the
 * short name first because it is what tells the two apart in the sidebar. The rest of
 * the row (telephone, website, signature, the caption printed in the footer) is edited
 * on the panel the page lands on right after, like the rest of the fiscal profile.
 */
interface IdentityField {
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
    | 'email'
  label: string
  required?: boolean
  hint?: string
}

const IDENTITY: readonly IdentityField[] = [
  { name: 'nome', label: 'Nome breve', required: true, hint: 'Come compare nel selettore e nelle liste' },
  { name: 'ragione_sociale', label: 'Ragione sociale', required: true },
  { name: 'partita_iva', label: 'Partita IVA', hint: '11 cifre per un’azienda italiana' },
  { name: 'codice_fiscale', label: 'Codice fiscale' },
  { name: 'indirizzo', label: 'Indirizzo' },
  { name: 'cap', label: 'CAP' },
  { name: 'comune', label: 'Comune' },
  { name: 'provincia', label: 'Provincia' },
  { name: 'nazione', label: 'Nazione', hint: 'Codice a due lettere: IT, GB, FR' },
  { name: 'pec', label: 'PEC' },
  { name: 'codice_sdi', label: 'Codice SDI', hint: '7 caratteri' },
  { name: 'email', label: 'Email' },
]

type IdentityName = IdentityField['name']

/**
 * The three regimes the settings' fiscal panel offers (REB-621), with the one VAT rate
 * each is usually opened with: the forfettario issues at zero with the server's own
 * natura and declaration, the ordinary regime at the standard rate and no natura, a
 * foreign company at its own country's rate. Everything else on the profile keeps
 * `FiscalProfileUpsert`'s defaults and is edited on the panel the page lands on.
 */
type Regime = 'RF19' | 'RF01' | 'estero'

const REGIMI: readonly { value: Regime; label: string; aliquota: string; natura: string }[] = [
  { value: 'RF19', label: 'Forfettario (RF19)', aliquota: '0.00', natura: 'N2.2' },
  { value: 'RF01', label: 'Ordinario (RF01)', aliquota: '22.00', natura: '' },
  // No rate proposed abroad: the country's own rate is the person's to type, and a
  // default that happened to be another country's would be stored unread.
  { value: 'estero', label: 'Estero: nessun regime italiano', aliquota: '', natura: '' },
]

interface Values {
  identity: Record<IdentityName, string>
  regime: Regime
  aliquota_iva_default: string
  natura_default: string
  iban: string
}

function emptyValues(): Values {
  const identity = Object.fromEntries(IDENTITY.map((field) => [field.name, ''])) as Record<
    IdentityName,
    string
  >
  return {
    identity: { ...identity, nazione: 'IT' },
    regime: 'RF19',
    aliquota_iva_default: '0.00',
    natura_default: 'N2.2',
    iban: '',
  }
}

/**
 * The fiscal half of the body. A key left out takes `FiscalProfileUpsert`'s default,
 * which is the forfettario's, so the two other regimes say explicitly what they do not
 * carry: no declaration of their own, and none of the three income parameters, which
 * are the forfettario's arithmetic and would otherwise make the estimate compute a
 * substitute tax for an SRL or a foreign company; abroad also no code and no bollo, as
 * the settings panel sends them. The natura is the person's, empty for «nothing»: the
 * server requires one beside a zero rate and discards one beside a non-zero rate, and
 * names the field either way.
 */
function fiscalBody(values: Values): Record<string, unknown> {
  const iban = values.iban.trim() || null
  const natura_default = values.natura_default.trim() || null
  const aliquota_iva_default = values.aliquota_iva_default.trim()
  if (values.regime === 'estero') {
    return {
      pack_id: 'non-it',
      codice_regime: null,
      aliquota_iva_default,
      natura_default,
      riferimento_normativo: null,
      applica_bollo: false,
      coefficiente_redditivita: null,
      aliquota_imposta_sostitutiva: null,
      aliquota_inps: null,
      iban,
    }
  }
  if (values.regime === 'RF01') {
    return {
      pack_id: 'it-flat-rate',
      codice_regime: 'RF01',
      aliquota_iva_default,
      natura_default,
      riferimento_normativo: null,
      coefficiente_redditivita: null,
      aliquota_imposta_sostitutiva: null,
      aliquota_inps: null,
      iban,
    }
  }
  return {
    pack_id: 'it-flat-rate',
    codice_regime: 'RF19',
    aliquota_iva_default,
    natura_default,
    iban,
  }
}

/** The request, as the hook sends it: the generated type lists every profile key the
 *  server defaults, and this form deliberately sends only the ones it asks for. */
function bodyFrom(values: Values): Record<string, unknown> {
  const identity = Object.fromEntries(
    IDENTITY.map((field) => {
      const typed = values.identity[field.name].trim()
      // An empty optional field is sent as `null`, never as `''`: the service refuses
      // an empty code it cannot normalise, and «nothing» is what the person meant.
      return [field.name, typed === '' && !field.required ? null : typed]
    }),
  ) as Record<IdentityName, string | null>
  return {
    ...identity,
    nome: values.identity.nome.trim(),
    ragione_sociale: values.identity.ragione_sociale.trim(),
    nazione: values.identity.nazione.trim().toUpperCase() || 'IT',
    fiscal_profile: fiscalBody(values),
  }
}

/**
 * «Nuova azienda» (REB-632): one form, one request, the row and its fiscal profile
 * together, as `POST /api/aziende` takes them (spec §3), so no azienda is ever born
 * that could not issue. On success the page lands on the new azienda's own panels,
 * where everything this form leaves at its default can be completed.
 */
export function NuovaAziendaForm({
  onCreated,
  onCancel,
}: {
  onCreated: (created: AziendaRecord) => void
  onCancel: () => void
}) {
  const create = useCreateAzienda()
  const [values, setValues] = useState<Values>(emptyValues)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)

  function submit() {
    setProblem(null)
    create.mutate(bodyFrom(values), {
      onSuccess: (created) => {
        toast.success(`Azienda «${created.nome}» creata`)
        onCreated(created)
      },
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  function pickRegime(regime: Regime) {
    const option = REGIMI.find((candidate) => candidate.value === regime)
    setValues((previous) => ({
      ...previous,
      regime,
      aliquota_iva_default: option?.aliquota ?? '0.00',
      natura_default: option?.natura ?? '',
    }))
  }

  const fieldError = problem ? fieldErrorFrom(problem) : null
  // A refusal on a key this form does not show (the natura the server infers, say)
  // goes to the banner rather than nowhere, as the settings' fiscal form does.
  const rendered = new Set<string>([
    ...IDENTITY.map((field) => field.name),
    'codice_regime',
    'pack_id',
    'aliquota_iva_default',
    'natura_default',
    'iban',
  ])
  const bannered = problem && (!fieldError || !rendered.has(fieldError.field))
  const regimeError =
    fieldError && (fieldError.field === 'codice_regime' || fieldError.field === 'pack_id')
      ? fieldError.message
      : null
  const incomplete =
    values.identity.nome.trim() === '' ||
    values.identity.ragione_sociale.trim() === '' ||
    values.aliquota_iva_default.trim() === ''

  return (
    <form
      className="space-y-6"
      aria-label="Nuova azienda"
      onSubmit={(event) => {
        event.preventDefault()
        submit()
      }}
    >
      <div>
        <h2 className="text-lg font-medium">Nuova azienda</h2>
        <p className="text-muted-foreground text-sm">
          Una seconda ragione sociale nello stesso spazio: avrà il suo registro fatture, i suoi
          clienti e la sua intestazione sui documenti. Nasce con il profilo fiscale qui sotto, che
          potrai completare subito dopo; i dati che già esistono non si spostano.
        </p>
      </div>

      {bannered ? <QueryErrorBanner error={problem} /> : null}

      <div className="grid gap-4 sm:grid-cols-2">
        {IDENTITY.map((field) => (
          <div key={field.name} className="space-y-2">
            <Label htmlFor={`nuova-${field.name}`}>
              {field.label}
              {field.required ? ' *' : ''}
            </Label>
            <Input
              id={`nuova-${field.name}`}
              value={values.identity[field.name]}
              disabled={create.isPending}
              aria-invalid={fieldError?.field === field.name ? true : undefined}
              onChange={(event) =>
                setValues((previous) => ({
                  ...previous,
                  identity: { ...previous.identity, [field.name]: event.target.value },
                }))
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

      <div className="space-y-2">
        <h3 className="font-medium">Fiscale</h3>
        <p className="text-muted-foreground text-sm">
          Il regime decide aliquota, natura e bollo di ogni fattura di questa azienda. Il resto del
          profilo (riferimento normativo, bollo, coefficienti) si completa nel pannello Fiscale.
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="nuova-regime">Regime</Label>
          <select
            id="nuova-regime"
            className="border-input bg-background h-9 w-full rounded-md border px-3 text-sm"
            value={values.regime}
            disabled={create.isPending}
            aria-invalid={regimeError ? true : undefined}
            onChange={(event) => pickRegime(event.target.value as Regime)}
          >
            {REGIMI.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          {regimeError ? (
            <p className="text-destructive text-sm">{regimeError}</p>
          ) : (
            <p className="text-muted-foreground text-xs">
              {values.regime === 'estero'
                ? 'Un’azienda con sede all’estero: niente fattura elettronica, niente bollo.'
                : 'Il codice che la fattura elettronica dichiara allo SdI.'}
            </p>
          )}
        </div>
        <div className="space-y-2">
          <Label htmlFor="nuova-aliquota_iva_default">Aliquota IVA predefinita</Label>
          <Input
            id="nuova-aliquota_iva_default"
            value={values.aliquota_iva_default}
            disabled={create.isPending}
            aria-invalid={fieldError?.field === 'aliquota_iva_default' ? true : undefined}
            onChange={(event) =>
              setValues((previous) => ({ ...previous, aliquota_iva_default: event.target.value }))
            }
          />
          {fieldError?.field === 'aliquota_iva_default' ? (
            <p className="text-destructive text-sm">{fieldError.message}</p>
          ) : (
            <p className="text-muted-foreground text-xs">
              {values.regime === 'estero'
                ? 'L’aliquota del paese in cui l’azienda ha sede, da indicare: 20.00 nel Regno Unito, 19.00 in Germania.'
                : 'In percentuale: 0.00 per il forfettario, 22.00 per l’ordinario.'}
            </p>
          )}
        </div>
        <div className="space-y-2">
          <Label htmlFor="nuova-natura_default">Natura</Label>
          <Input
            id="nuova-natura_default"
            value={values.natura_default}
            disabled={create.isPending}
            aria-invalid={fieldError?.field === 'natura_default' ? true : undefined}
            onChange={(event) =>
              setValues((previous) => ({ ...previous, natura_default: event.target.value }))
            }
          />
          {fieldError?.field === 'natura_default' ? (
            <p className="text-destructive text-sm">{fieldError.message}</p>
          ) : (
            <p className="text-muted-foreground text-xs">
              Obbligatoria con aliquota 0, da lasciare vuota con un’aliquota diversa da zero.
            </p>
          )}
        </div>
        <div className="space-y-2">
          <Label htmlFor="nuova-iban">IBAN</Label>
          <Input
            id="nuova-iban"
            value={values.iban}
            disabled={create.isPending}
            aria-invalid={fieldError?.field === 'iban' ? true : undefined}
            onChange={(event) =>
              setValues((previous) => ({ ...previous, iban: event.target.value }))
            }
          />
          {fieldError?.field === 'iban' ? (
            <p className="text-destructive text-sm">{fieldError.message}</p>
          ) : (
            <p className="text-muted-foreground text-xs">
              Quello stampato sulle fatture e nei solleciti di questa azienda.
            </p>
          )}
        </div>
      </div>

      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={onCancel} disabled={create.isPending}>
          Annulla
        </Button>
        <Button type="submit" disabled={create.isPending || incomplete}>
          Crea azienda
        </Button>
      </div>
    </form>
  )
}
