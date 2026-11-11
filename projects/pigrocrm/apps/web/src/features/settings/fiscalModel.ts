import type { FiscalProfile } from '@/features/invoices/queries'

/**
 * The fiscal profile's form model: the regime choice, the field table, and the pure
 * conversions between the API's row, the form's draft and the body a save sends.
 * `FiscalForm` draws and edits it; nothing here renders.
 */

export interface FiscalField {
  name:
    | 'default_vat_rate'
    | 'default_vat_nature'
    | 'legal_reference'
    | 'stamp_duty_threshold'
    | 'stamp_duty_amount'
    | 'payment_terms'
    | 'payment_method'
    | 'due_days'
    | 'iban'
    | 'profitability_coefficient'
    | 'substitute_tax_rate'
    | 'social_security_rate'
    | 'flat_rate_start_year'
  label: string
  hint?: string
  /** The regimes that read the field; every regime when absent. A field a regime does not
   *  read is hidden, and sent empty where the server refuses a value (REB-621, REB-760). */
  shownFor?: readonly Regime[]
}

/**
 * The regime choice (REB-621, spec 2026-10-03 §1.3; REB-760): the two Italian codes the
 * server implements, each on the pack of its own (`it-flat-rate` for the forfettario,
 * `it-standard` for the ordinary regime, `it-company` for an srl, which invoices as `RF01`
 * too and differs only in the taxes it owes, REB-1005), and the `non-it` pack, which has no FatturaPA
 * code at all. A select and not the free RF field this panel had: `resolve_regime`
 * refuses every other code anyway («non implementato»), so a free field only offered
 * ways to be refused.
 */
export type Regime = 'RF19' | 'RF01' | 'company' | 'foreign'
type PackId = 'it-flat-rate' | 'it-standard' | 'it-company' | 'non-it'

export const Regimes: readonly { value: Regime; label: string }[] = [
  { value: 'RF19', label: 'Forfettario (RF19)' },
  { value: 'RF01', label: 'Ordinario (RF01)' },
  { value: 'company', label: 'Srl (RF01)' },
  { value: 'foreign', label: 'Estero: nessun regime italiano' },
]

/** The ordinary regime's standard VAT rate (art. 16 DPR 633/1972), the one the server
 *  proposes for `it-standard` and the panel offers when the regime is picked. */
export const ORDINARY_RATE = '22.00'

export const PACK_OF: Record<Regime, PackId> = {
  RF19: 'it-flat-rate',
  RF01: 'it-standard',
  company: 'it-company',
  foreign: 'non-it',
}

export function regimeOf(values: { pack_id: PackId }): Regime {
  if (values.pack_id === 'non-it') return 'foreign'
  if (values.pack_id === 'it-company') return 'company'
  return values.pack_id === 'it-standard' ? 'RF01' : 'RF19'
}

function isPackId(value: string): value is PackId {
  return (
    value === 'it-flat-rate' ||
    value === 'it-standard' ||
    value === 'it-company' ||
    value === 'non-it'
  )
}

/**
 * `FiscalProfileUpsert.legal_reference`'s own default, repeated here because the
 * panel PUTs every key on every save and the server's default therefore never applies.
 * Seeding this blank -- as this form did until the income columns arrived -- meant a
 * first save stored an empty normative reference: harmless only by accident, because
 * `_flat_rate.resolve_line_vat` treats the empty string as falsy and falls back to
 * this same sentence at emission time. Better to show the owner the sentence their
 * invoices will actually carry, where they can read and amend it, than to store a blank
 * and rely on a fallback three layers away.
 */
const LegalReferenceFlatRate =
  "Operazione non soggetta a IVA ai sensi dell'art. 1, commi 54-89, " +
  'L. 190/2014 - regime forfettario'

/**
 * `apply_stamp_duty` is a boolean and lives outside this list; everything else is a text
 * input because the values are codes and decimals the backend validates, and a
 * client-side guess at their shape would only produce a second, weaker validator.
 */
export const FIELDS: readonly FiscalField[] = [
  { name: 'default_vat_rate', label: 'Aliquota IVA predefinita' },
  // VAT nature and legal reference stay on «Estero» too: a foreign company whose untaxed line
  // must carry a legal mention names it here, and the server requires a VAT nature beside
  // a zero rate whoever issues (`invoice_lines`' own rule). Emptied when the regime
  // turns foreign, since «N2.2» is the flat-rate regime's. The ordinary regime taxes every
  // line and carries neither.
  {
    name: 'default_vat_nature',
    label: 'Natura',
    hint: 'Obbligatoria quando l’aliquota è 0',
    shownFor: ['RF19', 'foreign'],
  },
  { name: 'legal_reference', label: 'Riferimento normativo', shownFor: ['RF19', 'foreign'] },
  { name: 'stamp_duty_threshold', label: 'Soglia bollo', shownFor: ['RF19'] },
  { name: 'stamp_duty_amount', label: 'Importo bollo', shownFor: ['RF19'] },
  { name: 'payment_terms', label: 'Condizioni di pagamento', hint: 'TP02' },
  { name: 'payment_method', label: 'Modalità di pagamento', hint: 'MP05' },
  { name: 'due_days', label: 'Giorni di scadenza' },
  { name: 'iban', label: 'IBAN' },
  // The three income-calculation columns. They decide nothing about an invoice -- they
  // are what the fiscal estimate divides the year's takings by -- but they live on the
  // same single row, so this is the only screen that can ever set them. Percentages,
  // the way their owner reads them off a commercialista's letter: `67`, not `0.67`.
  // The forfettario's arithmetic alone: no other regime has an estimate.
  {
    name: 'profitability_coefficient',
    label: 'Coefficiente di redditività',
    hint: 'La quota dei ricavi che diventa reddito imponibile, secondo il codice ATECO',
    shownFor: ['RF19'],
  },
  {
    name: 'substitute_tax_rate',
    label: 'Aliquota imposta sostitutiva',
    hint: 'Sostituisce IRPEF e addizionali: 5% nei primi cinque anni, poi 15%',
    shownFor: ['RF19'],
  },
  {
    name: 'social_security_rate',
    label: 'Aliquota INPS',
    hint: 'I contributi previdenziali calcolati sul reddito imponibile',
    shownFor: ['RF19'],
  },
  {
    name: 'flat_rate_start_year',
    label: 'Anno di inizio attività',
    hint: 'Con le condizioni sotto decide l’aliquota agevolata dei primi anni (legge 190/2014, art. 1 comma 65)',
    shownFor: ['RF19'],
  },
]

/** The facts the INPS and flat-rate law read (REB-1070), each one a choice: a closed list, or
 *  yes/no/not stated for a nullable boolean (`''` is «not stated», never a default). */
export type ChoiceName =
  | 'inps_category'
  | 'inps_trade'
  | 'inps_covered_elsewhere'
  | 'inps_before_1996'
  | 'flat_rate_inps_reduction'
  | 'isa'
  | 'ateco_group'
  | 'startup_no_prior_activity'
  | 'startup_not_a_continuation'
  | 'startup_predecessor_within_limit'

export interface ChoiceField {
  name: ChoiceName
  label: string
  hint: string
  /** The packs that read the fact, as regimes: the srl and a company abroad have none. */
  shownFor: readonly Regime[]
  /** Shown only while this holds on the draft: a pairing the law makes. */
  when?: (values: Values) => boolean
  /** `null` for the annex 4 group, whose options come from the pack on the profile read. */
  options: readonly { value: string; label: string }[] | null
}

const YES_NO = [
  { value: '', label: 'Non indicato' },
  { value: 'true', label: 'Sì' },
  { value: 'false', label: 'No' },
] as const

const PERSON: readonly Regime[] = ['RF19', 'RF01']
const artisanOrTrader = (values: Values) => values.inps_category === 'artigiani_commercianti'

export const CHOICES: readonly ChoiceField[] = [
  {
    name: 'inps_category',
    label: 'Cassa INPS',
    hint: 'La gestione a cui versi i contributi. Senza, le tasse non calcolano i contributi.',
    shownFor: PERSON,
    options: [
      { value: '', label: 'Non indicata' },
      { value: 'gestione_separata', label: 'Gestione separata' },
      { value: 'artigiani_commercianti', label: 'Artigiani e commercianti' },
    ],
  },
  {
    name: 'inps_trade',
    label: 'Gestione INPS',
    hint: 'Artigiani e commercianti hanno aliquote diverse.',
    shownFor: PERSON,
    when: artisanOrTrader,
    options: [
      { value: '', label: 'Non indicata' },
      { value: 'artigiani', label: 'Artigiani' },
      { value: 'commercianti', label: 'Commercianti' },
    ],
  },
  {
    name: 'inps_covered_elsewhere',
    label: 'Assicurato altrove o pensionato',
    hint: 'La gestione separata applica un’aliquota più bassa a chi ha già un’altra copertura.',
    shownFor: PERSON,
    when: (values) => values.inps_category === 'gestione_separata',
    options: YES_NO,
  },
  {
    name: 'inps_before_1996',
    label: 'Contributi versati prima del 1996',
    hint: 'Per chi aveva contributi al 31 dicembre 1995 il massimale contributivo è diverso.',
    shownFor: PERSON,
    when: artisanOrTrader,
    options: YES_NO,
  },
  {
    name: 'flat_rate_inps_reduction',
    label: 'Riduzione del 35% dei contributi',
    hint: 'Se hai chiesto la riduzione contributiva di artigiani e commercianti nel forfettario.',
    shownFor: ['RF19'],
    when: artisanOrTrader,
    options: YES_NO,
  },
  {
    name: 'isa',
    label: 'Soggetto agli ISA',
    hint: 'Decide come si dividono gli acconti delle imposte sui redditi.',
    shownFor: PERSON,
    options: YES_NO,
  },
  {
    name: 'ateco_group',
    label: 'Attività (gruppo ATECO)',
    hint: 'Il gruppo dell’allegato 4 della legge 190/2014 che corrisponde al tuo codice ATECO 2007.',
    shownFor: ['RF19'],
    options: null,
  },
  {
    name: 'startup_no_prior_activity',
    label: 'Nessuna attività nei tre anni prima dell’inizio',
    hint: 'Aliquota agevolata: non hai esercitato attività artistica, professionale o d’impresa.',
    shownFor: ['RF19'],
    options: YES_NO,
  },
  {
    name: 'startup_not_a_continuation',
    label: 'Non è la prosecuzione di un lavoro precedente',
    hint: 'Aliquota agevolata: l’attività non continua un lavoro dipendente o autonomo, salvo il praticantato obbligatorio.',
    shownFor: ['RF19'],
    options: YES_NO,
  },
  {
    name: 'startup_predecessor_within_limit',
    label: 'Ricavi di chi ti ha preceduto entro il limite',
    hint: 'Aliquota agevolata: se prosegui l’attività di un altro soggetto, i suoi ricavi dell’anno prima non superano la soglia del forfettario.',
    shownFor: ['RF19'],
    options: YES_NO,
  },
]

/** The choices that are a nullable boolean on the server: `''` goes as `null`. */
const BOOLEAN_CHOICES: readonly ChoiceName[] = [
  'inps_covered_elsewhere',
  'inps_before_1996',
  'flat_rate_inps_reduction',
  'isa',
  'startup_no_prior_activity',
  'startup_not_a_continuation',
  'startup_predecessor_within_limit',
]

export type Values = Record<FiscalField['name'], string> &
  Record<ChoiceName, string> & {
  apply_stamp_duty: boolean
  pack_id: PackId
  regime_code: string
}

export type ValueName = keyof Values

/** The reloaded profile under the draft: a value the person changed keeps the draft,
 *  every other one takes the row's. The regime select writes `pack_id` and
 *  `regime_code` together, so both are marked when it is picked. */
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
    regime_code: 'RF19',
    default_vat_rate: '0.00',
    default_vat_nature: 'N2.2',
    legal_reference: LegalReferenceFlatRate,
    stamp_duty_threshold: '77.47',
    stamp_duty_amount: '2.00',
    payment_terms: 'TP02',
    payment_method: 'MP05',
    due_days: '30',
    iban: '',
    // `FiscalProfileUpsert`'s own defaults, and they have to be repeated here for the
    // same reason every other value on this list is: the save is a full replace, so a
    // key this form does not know about is not left alone -- it is written back as
    // whatever the form last held for it. Before these three existed here, pressing
    // Salva reset all of them to the server's defaults, which was invisible only
    // because nothing had ever changed them yet.
    profitability_coefficient: '67.00',
    substitute_tax_rate: '5.00',
    social_security_rate: '26.07',
    flat_rate_start_year: '',
    inps_category: '',
    inps_trade: '',
    inps_covered_elsewhere: '',
    inps_before_1996: '',
    flat_rate_inps_reduction: '',
    isa: '',
    ateco_group: '',
    startup_no_prior_activity: '',
    startup_not_a_continuation: '',
    startup_predecessor_within_limit: '',
    apply_stamp_duty: true,
  }
}

export function valuesFrom(profile: FiscalProfile): Values {
  const values = emptyValues()
  for (const field of FIELDS) {
    const stored = profile[field.name]
    values[field.name] = stored === null || stored === undefined ? '' : String(stored)
  }
  for (const choice of CHOICES) {
    const stored = profile[choice.name]
    values[choice.name] = stored === null || stored === undefined ? '' : String(stored)
  }
  values.apply_stamp_duty = profile.apply_stamp_duty
  values.pack_id = isPackId(profile.pack_id) ? profile.pack_id : 'it-flat-rate'
  values.regime_code = profile.regime_code ?? ''
  return values
}

/**
 * What a save sends. The same fifteen keys whichever the regime, because the PUT is a
 * full replace and a key left out is a key the server defaults; on any regime but the
 * flat-rate regime the ones only it reads go explicitly empty, since the server's own
 * defaults for them are the flat-rate regime's («N2.2», the stamp duty on, the ATECO coefficient)
 * and a profile that carried them would be refused or, worse, would estimate the
 * flat-rate regime's taxes. «Estero» also has no code and keeps its VAT nature; the ordinary
 * regime sends `RF01` and no VAT nature, since it taxes every line.
 */
export function bodyFrom(values: Values): Record<string, unknown> {
  const { pack_id, regime_code, ...fields } = values
  // The bollo amounts are hidden outside the flat-rate regime and the server still wants
  // decimals for them: a blank left from an edit under another regime goes as the default.
  const rest = {
    ...fields,
    ...taxpayerFacts(fields, pack_id),
    stamp_duty_threshold: fields.stamp_duty_threshold.trim() || '77.47',
    stamp_duty_amount: fields.stamp_duty_amount.trim() || '2.00',
  }
  if (pack_id === 'non-it') {
    return {
      ...rest,
      pack_id,
      regime_code: null,
      default_vat_nature: rest.default_vat_nature || null,
      legal_reference: rest.legal_reference || null,
      apply_stamp_duty: false,
      profitability_coefficient: null,
      substitute_tax_rate: null,
      social_security_rate: null,
    }
  }
  if (pack_id === 'it-standard' || pack_id === 'it-company') {
    return {
      ...rest,
      pack_id,
      regime_code: 'RF01',
      default_vat_nature: null,
      legal_reference: null,
      apply_stamp_duty: false,
      profitability_coefficient: null,
      substitute_tax_rate: null,
      social_security_rate: null,
    }
  }
  return { ...rest, pack_id, regime_code }
}

/**
 * The taxpayer facts of a save, as the server reads them: `''` is «not stated» and goes as
 * `null`, a yes/no as a boolean, the start year as a number. A fact the pack does not read goes
 * `null` too (the server refuses a value there), and so does one whose pairing is not met
 * (a trade with the gestione separata), so a draft that was edited and then changed back never
 * sends a stale answer.
 */
function taxpayerFacts(
  values: Omit<Values, 'pack_id' | 'regime_code'>,
  pack: PackId,
): Record<string, unknown> {
  const shown = new Set<string>(
    CHOICES.filter(
      (choice) =>
        choice.shownFor.includes(regimeOf({ pack_id: pack })) &&
        (choice.when?.({ ...values, pack_id: pack, regime_code: '' }) ?? true),
    ).map((choice) => choice.name),
  )
  const facts: Record<string, unknown> = {}
  for (const choice of CHOICES) {
    // The category and the ISA flag are older than the form: an srl keeps what its row holds,
    // and only a company abroad, which the server refuses a value for, sends them empty.
    const kept =
      (choice.name === 'inps_category' || choice.name === 'isa') && pack !== 'non-it'
    const raw = shown.has(choice.name) || kept ? values[choice.name] : ''
    facts[choice.name] = BOOLEAN_CHOICES.includes(choice.name)
      ? raw === '' ? null : raw === 'true'
      : raw === '' ? null : raw
  }
  const year = pack === 'it-flat-rate' ? values.flat_rate_start_year.trim() : ''
  // A year that is not four digits goes as typed, for the server to refuse by field name.
  facts.flat_rate_start_year = year === '' ? null : /^\d{4}$/.test(year) ? Number(year) : year
  return facts
}
