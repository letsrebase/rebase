import type { FiscalProfile } from '@/features/invoices/queries'

/**
 * The fiscal profile's form model: the regime choice, the field table, and the pure
 * conversions between the API's row, the form's draft and the body a save sends.
 * `FiscalForm` draws and edits it; nothing here renders.
 */

export interface FiscalField {
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
  /** The regimes that read the field; every regime when absent. A field a regime does not
   *  read is hidden, and sent empty where the server refuses a value (REB-621, REB-760). */
  shownFor?: readonly Regime[]
}

/**
 * The regime choice (REB-621, spec 2026-10-03 §1.3; REB-760): the two Italian codes the
 * server implements, each on the pack of its own (`it-flat-rate` for the forfettario,
 * `it-standard` for the ordinary regime), and the `non-it` pack, which has no FatturaPA
 * code at all. A select and not the free RF field this panel had: `resolve_regime`
 * refuses every other code anyway («non implementato»), so a free field only offered
 * ways to be refused.
 */
export type Regime = 'RF19' | 'RF01' | 'estero'
type PackId = 'it-flat-rate' | 'it-standard' | 'non-it'

export const REGIMI: readonly { value: Regime; label: string }[] = [
  { value: 'RF19', label: 'Forfettario (RF19)' },
  { value: 'RF01', label: 'Ordinario (RF01)' },
  { value: 'estero', label: 'Estero: nessun regime italiano' },
]

/** The ordinary regime's standard VAT rate (art. 16 DPR 633/1972), the one the server
 *  proposes for `it-standard` and the panel offers when the regime is picked. */
export const ORDINARY_RATE = '22.00'

export const PACK_OF: Record<Regime, PackId> = {
  RF19: 'it-flat-rate',
  RF01: 'it-standard',
  estero: 'non-it',
}

export function regimeOf(values: { pack_id: PackId }): Regime {
  if (values.pack_id === 'non-it') return 'estero'
  return values.pack_id === 'it-standard' ? 'RF01' : 'RF19'
}

function isPackId(value: string): value is PackId {
  return value === 'it-flat-rate' || value === 'it-standard' || value === 'non-it'
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
export const FIELDS: readonly FiscalField[] = [
  { name: 'aliquota_iva_default', label: 'Aliquota IVA predefinita' },
  // Natura and riferimento stay on «Estero» too: a foreign company whose untaxed line
  // must carry a legal mention names it here, and the server requires a natura beside
  // a zero rate whoever issues (`invoice_lines`' own rule). Emptied when the regime
  // turns foreign, since «N2.2» is the forfettario's. The ordinary regime taxes every
  // line and carries neither.
  {
    name: 'natura_default',
    label: 'Natura',
    hint: 'Obbligatoria quando l’aliquota è 0',
    shownFor: ['RF19', 'estero'],
  },
  { name: 'riferimento_normativo', label: 'Riferimento normativo', shownFor: ['RF19', 'estero'] },
  { name: 'soglia_bollo', label: 'Soglia bollo', shownFor: ['RF19'] },
  { name: 'importo_bollo', label: 'Importo bollo', shownFor: ['RF19'] },
  { name: 'condizioni_pagamento', label: 'Condizioni di pagamento', hint: 'TP02' },
  { name: 'modalita_pagamento', label: 'Modalità di pagamento', hint: 'MP05' },
  { name: 'giorni_scadenza', label: 'Giorni di scadenza' },
  { name: 'iban', label: 'IBAN' },
  // The three income-calculation columns. They decide nothing about an invoice -- they
  // are what the fiscal estimate divides the year's takings by -- but they live on the
  // same single row, so this is the only screen that can ever set them. Percentages,
  // the way their owner reads them off a commercialista's letter: `67`, not `0.67`.
  // The forfettario's arithmetic alone: no other regime has an estimate.
  {
    name: 'coefficiente_redditivita',
    label: 'Coefficiente di redditività',
    hint: 'La quota dei ricavi che diventa reddito imponibile, secondo il codice ATECO',
    shownFor: ['RF19'],
  },
  {
    name: 'aliquota_imposta_sostitutiva',
    label: 'Aliquota imposta sostitutiva',
    hint: 'Sostituisce IRPEF e addizionali: 5% nei primi cinque anni, poi 15%',
    shownFor: ['RF19'],
  },
  {
    name: 'aliquota_inps',
    label: 'Aliquota INPS',
    hint: 'I contributi previdenziali calcolati sul reddito imponibile',
    shownFor: ['RF19'],
  },
]

export type Values = Record<FiscalField['name'], string> & {
  applica_bollo: boolean
  pack_id: PackId
  codice_regime: string
}

export type ValueName = keyof Values

/** The reloaded profile under the draft: a value the person changed keeps the draft,
 *  every other one takes the row's. The regime select writes `pack_id` and
 *  `codice_regime` together, so both are marked when it is picked. */
export function withDraft(fresh: Values, draft: Values, touched: ReadonlySet<ValueName>): Values {
  const merged: Values = { ...fresh }
  for (const name of touched) {
    // One assignment per key, typed by the key: `Values` is a record of strings plus
    // two non-string members, and a loop over `keyof` cannot narrow them one by one.
    ;(merged as Record<ValueName, unknown>)[name] = draft[name]
  }
  return merged
}

export function emptyValues(): Values {
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

export function valuesFrom(profile: FiscalProfile): Values {
  const values = emptyValues()
  for (const field of FIELDS) {
    const stored = profile[field.name]
    values[field.name] = stored === null || stored === undefined ? '' : String(stored)
  }
  values.applica_bollo = profile.applica_bollo
  values.pack_id = isPackId(profile.pack_id) ? profile.pack_id : 'it-flat-rate'
  values.codice_regime = profile.codice_regime ?? ''
  return values
}

/**
 * What a save sends. The same fifteen keys whichever the regime, because the PUT is a
 * full replace and a key left out is a key the server defaults; on any regime but the
 * forfettario the ones only it reads go explicitly empty, since the server's own
 * defaults for them are the forfettario's («N2.2», the bollo on, the ATECO coefficient)
 * and a profile that carried them would be refused or, worse, would estimate the
 * forfettario's taxes. «Estero» also has no code and keeps its natura; the ordinary
 * regime sends `RF01` and no natura, since it taxes every line.
 */
export function bodyFrom(values: Values): Record<string, unknown> {
  const { pack_id, codice_regime, ...fields } = values
  // The bollo amounts are hidden outside the forfettario and the server still wants
  // decimals for them: a blank left from an edit under another regime goes as the default.
  const rest = {
    ...fields,
    soglia_bollo: fields.soglia_bollo.trim() || '77.47',
    importo_bollo: fields.importo_bollo.trim() || '2.00',
  }
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
  if (pack_id === 'it-standard') {
    return {
      ...rest,
      pack_id,
      codice_regime: 'RF01',
      natura_default: null,
      riferimento_normativo: null,
      applica_bollo: false,
      coefficiente_redditivita: null,
      aliquota_imposta_sostitutiva: null,
      aliquota_inps: null,
    }
  }
  return { ...rest, pack_id, codice_regime }
}
