/**
 * The drafts the matches pages edit (REB-387): an input always holds a string, the API
 * takes typed values, and these helpers are the one place the two meet. Kept out of the
 * page files, which export components only (`react-refresh/only-export-components`).
 */
import {
  ApiError,
  LETTER_TEXT_KEYS,
  type Customer,
  type CustomerDraft,
  type Company,
  type ContractDocument,
  type Fiscal,
  type FiscalData,
  type Letter,
  type LetterDraft,
  type LetterTextKey,
  type Match,
  type Regime,
  type SendReport,
} from './api'
import { amountNumber, sentAmount } from './amount'
import { formatDate } from './format'

/** The tax form as typed: every field a string, and the box for an occasional engagement
 *  with no VAT number (REB-687), ticked exactly when the VAT number is saved as `null`. */
export type FiscalTextKey = keyof FiscalData
export type FiscalDraft = Record<FiscalTextKey, string> & { without_vat_number: boolean }

export const FISCAL_EMPTY: FiscalDraft = {
  tax_code: '',
  vat_number: '',
  domicile: '',
  certified_email: '',
  without_vat_number: false,
}

export function draftFromFiscal(fiscal: Fiscal | null): FiscalDraft {
  if (fiscal === null) return FISCAL_EMPTY
  return {
    tax_code: fiscal.tax_code,
    vat_number: fiscal.vat_number ?? '',
    domicile: fiscal.domicile,
    certified_email: fiscal.certified_email ?? '',
    without_vat_number: fiscal.vat_number === null,
  }
}

export type FiscalKey = keyof FiscalDraft

/** The fields one change of the form typed in. */
export function typedFiscalFields(before: FiscalDraft, after: FiscalDraft): FiscalKey[] {
  return (Object.keys(after) as FiscalKey[]).filter((key) => after[key] !== before[key])
}

/** A form refilled from a newer saved record: the fields the admin typed in since keep
 *  their text, the others take the record's values, so no field left alone keeps a value
 *  older than the record, for a later save to write back. */
export function refillFiscal(draft: FiscalDraft, fiscal: Fiscal | null, typed: ReadonlySet<FiscalKey>): FiscalDraft {
  const fresh = draftFromFiscal(fiscal)
  const keys = Object.keys(fresh) as FiscalKey[]
  return Object.fromEntries(keys.map((key) => [key, typed.has(key) ? draft[key] : fresh[key]])) as FiscalDraft
}

/** Whether `record` was read before `held`, the tax record the page already holds: a
 *  prefill that read the tax data before a save the page already has back must not put
 *  them back. No record is older than any. */
export function olderFiscal(record: Fiscal | null, held: Fiscal | null): boolean {
  if (held === null) return false
  if (record === null) return true
  return Date.parse(record.updated_at) < Date.parse(held.updated_at)
}

/** Whether `record` is newer than `held`, the tax record a form was last filled from:
 *  only a newer one refills it, so a refetch older than a save, or no record at all,
 *  never puts older values back. */
export function newerFiscal(record: Fiscal | null, held: Fiscal | null): record is Fiscal {
  if (record === null) return false
  return held === null || Date.parse(record.updated_at) > Date.parse(held.updated_at)
}

/** A field as a save carries it: the box is whether the partita IVA went as `null`. */
function savedValue(data: FiscalData, key: FiscalKey): string | boolean | null {
  return key === 'without_vat_number' ? data.vat_number === null : data[key]
}

/** The typed fields a save did not cover, because their text changed after it was sent
 *  (compared as sent, trimmed): once the save succeeds only these stay typed, and the
 *  others take the record that comes back. */
export function typedAfterSave(draft: FiscalDraft, typed: ReadonlySet<FiscalKey>, sent: FiscalData): Set<FiscalKey> {
  const now = toFiscalData(draft)
  return new Set([...typed].filter((key) => savedValue(now, key) !== savedValue(sent, key)))
}

/** An empty PEC is `null`, not `""`, which the API would try to read as an address; the
 *  ticked box is no partita IVA, `null` (REB-687). */
export function toFiscalData(draft: FiscalDraft): FiscalData {
  return {
    tax_code: draft.tax_code.trim(),
    vat_number: draft.without_vat_number ? null : draft.vat_number.trim(),
    domicile: draft.domicile.trim(),
    certified_email: draft.certified_email.trim() || null,
  }
}

/** Saved tax data in one line: «Chi e per chi» shows it instead of their fields, and
 *  «Dati fiscali» on «Match e contratti» in its closed summary. */
export function fiscalLine(fiscal: FiscalData): string {
  const vat_number = fiscal.vat_number ? `P.IVA ${fiscal.vat_number}` : 'senza partita IVA'
  return `Salvati: CF ${fiscal.tax_code} · ${vat_number}`
}

/** What «Avanti» on «Chi e per chi» saves: the fields when nothing was saved yet, or
 *  when the admin opened the saved ones and changed them; `null` otherwise, so data only
 *  shown are never written again under this admin's name. `null` and an empty string
 *  are the same value (REB-687): a partita IVA saved as `null` and shown as the ticked
 *  box is not saved again. */
export function fiscalToSave(saved: FiscalData | null, draft: FiscalDraft, editing: boolean): FiscalData | null {
  const data = toFiscalData(draft)
  if (saved === null) return data
  if (!editing) return null
  const same = (Object.keys(data) as (keyof FiscalData)[]).every((key) => (saved[key] || null) === (data[key] || null))
  return same ? null : data
}

export type CustomerForm = Record<keyof Customer, string>
export const CUSTOMER_EMPTY: CustomerForm = { customer_legal_name: '', customer_vat_number: '', customer_registered_office: '' }

export function customerForm(draft: CustomerDraft): CustomerForm {
  return {
    customer_legal_name: draft.customer_legal_name ?? '',
    customer_vat_number: draft.customer_vat_number ?? '',
    customer_registered_office: draft.customer_registered_office ?? '',
  }
}

export function toCustomer(form: CustomerForm): Customer {
  return {
    customer_legal_name: form.customer_legal_name.trim(),
    customer_vat_number: form.customer_vat_number.trim(),
    customer_registered_office: form.customer_registered_office.trim(),
  }
}

/** The request «Chi e per chi» folds its list into once one is picked. Never the
 *  budget, which this flow does not show (spec § 1h). */
export function requestLine(company: Company): string {
  return `${company.company_name} · ${company.contact_person} · ${company.requested_role} · dal ${formatDate(company.period_from)}`
}

/** What a refusal can name that «Chi e per chi» asks: the client on the letter, and the
 *  freelancer's tax data (`tax` when they are missing altogether). */
export const CUSTOMER_FIELDS: ReadonlySet<string> = new Set<keyof Customer>([
  'customer_legal_name',
  'customer_vat_number',
  'customer_registered_office',
])
export const FISCAL_FIELDS: ReadonlySet<string> = new Set(['tax', ...Object.keys(FISCAL_EMPTY)])

/** A client with all three details reads as one line; one missing asks for the fields. */
export function customerComplete(form: CustomerForm): boolean {
  return Object.values(toCustomer(form)).every(Boolean)
}

export function customerLine(form: CustomerForm): string {
  const customer = toCustomer(form)
  return `${customer.customer_legal_name} · P.IVA ${customer.customer_vat_number} · ${customer.customer_registered_office}`
}

export type LetterForm = Record<LetterTextKey, string> & {
  start_date: string
  end_date: string
  fee: string
  payment_days: string
  month_end: boolean
  notice_days: string
}
export type LetterFieldKey = keyof LetterForm
/** A field an input or a textarea holds: every one but `month_end`, the checkbox. */
export type LetterTextFieldKey = Exclude<LetterFieldKey, 'month_end'>

const TEXT_EMPTY = Object.fromEntries(LETTER_TEXT_KEYS.map((key) => [key, ''])) as Record<LetterTextKey, string>

export const LETTER_EMPTY: LetterForm = {
  ...TEXT_EMPTY,
  start_date: '',
  end_date: '',
  fee: '',
  payment_days: '',
  month_end: false,
  notice_days: '',
}

/** The API's amount as the fee field shows it, the way the letter writes it: «480» for
 *  «480.00», «480,50» for «480.50». `toLetter` reads the comma back. */
export function amountForm(value: string): string {
  const [whole = '', cents = ''] = value.trim().split('.')
  const significant = cents.replace(/0+$/, '')
  return significant ? `${whole},${significant.padEnd(2, '0')}` : whole
}

export function letterForm(draft: LetterDraft): LetterForm {
  const text = Object.fromEntries(LETTER_TEXT_KEYS.map((key) => [key, draft[key] ?? ''])) as Record<LetterTextKey, string>
  return {
    ...text,
    start_date: draft.start_date ?? '',
    end_date: draft.end_date ?? '',
    fee: draft.fee === null ? '' : amountForm(draft.fee),
    payment_days: draft.payment_days === null ? '' : String(draft.payment_days),
    month_end: draft.month_end ?? false,
    notice_days: draft.notice_days === null ? '' : String(draft.notice_days),
  }
}

export function toLetter(form: LetterForm): Letter {
  const text = Object.fromEntries(LETTER_TEXT_KEYS.map((key) => [key, form[key].trim() || null])) as Record<
    LetterTextKey,
    string | null
  >
  return {
    ...text,
    start_date: form.start_date,
    end_date: form.end_date || null,
    fee: sentAmount(form.fee),
    payment_days: Number(form.payment_days),
    month_end: form.month_end,
    notice_days: form.notice_days.trim() ? Number(form.notice_days) : null,
  }
}

/** «Giorni previsti» (REB-497), the admin's estimate of the engagement's billable days,
 *  as `MatchCreate` takes it: a number, or `null` for an empty box (and for text that is
 *  no number, which JSON would send as `null` anyway). A fraction or a number out of range
 *  goes as typed, for the API to refuse by its field. The wizard keeps the box beside
 *  `LetterForm`, never in it, so `toLetter` cannot send it: `LetterFields` refuses a
 *  key it does not know, and nothing in the letter changes with this number. */
export function expectedDaysToSend(text: string): number | null {
  const value = text.trim()
  const days = Number(value)
  return value && Number.isFinite(days) ? days : null
}

/** The letter's fields as «Crea match» names them. The client's budget has no label
 *  here, because it has no field anywhere in this flow (spec § 1h). The page asks
 *  `mode` and `unit` as one choice, «Come si paga», and `payment_days` inside
 *  its sentence, «Pagamento a … giorni», so those three labels show nowhere on it. */
export const LETTER_LABELS: Record<LetterFieldKey, string> = {
  role: 'Ruolo',
  activities: 'Cosa farà',
  results: 'Risultati da consegnare',
  acceptance: 'Come il cliente accetta i risultati',
  start_date: 'Inizio',
  end_date: 'Fine prevista (facoltativa)',
  commitment: 'Impegno',
  review_period: 'Periodo iniziale di verifica',
  location: 'Dove',
  coordination: 'Coordinamento concordato con il cliente',
  customer_contact: 'Referente del cliente',
  rebase_contact: 'Referente di rebase',
  mode: 'Modalità',
  fee: 'Compenso (€)',
  unit: 'Unità del compenso',
  extra_work: 'Lavoro festivo o fuori fascia',
  expenses: 'Spese',
  payment_days: 'Giorni di pagamento',
  month_end: 'fine mese',
  billing_deadlines: 'Scadenze di fatturazione',
  notice_days: 'Giorni di preavviso',
  personal_data: 'Tratta dati personali del cliente',
  purpose_data: 'Natura e finalità del trattamento',
  category_data: 'Categorie di dati',
  data_subjects: 'Categorie di interessati',
  authorization_data: 'Autorizzazione scritta del cliente alla nomina',
  exclusivity: 'Esclusiva verso il cliente',
  portfolio: 'Citazione nel portfolio',
  insurance: 'Assicurazione di responsabilità civile professionale',
  other_terms: 'Altre condizioni',
  previous_reports: 'Rapporti precedenti con il cliente',
}

/** The letter's own sections, the order «Altre condizioni» keeps. */
export const LETTER_GROUPS: readonly { title: string; fields: readonly LetterFieldKey[] }[] = [
  { title: 'Attività', fields: ['role', 'activities', 'results', 'acceptance'] },
  { title: 'Tempi e impegno', fields: ['start_date', 'end_date', 'commitment', 'review_period'] },
  { title: 'Modalità di lavoro', fields: ['location', 'coordination', 'customer_contact', 'rebase_contact'] },
  {
    title: 'Condizioni economiche',
    fields: ['mode', 'fee', 'unit', 'extra_work', 'expenses', 'payment_days', 'month_end', 'billing_deadlines'],
  },
  { title: 'Preavviso', fields: ['notice_days'] },
  {
    title: 'Dati personali',
    fields: ['personal_data', 'purpose_data', 'category_data', 'data_subjects', 'authorization_data'],
  },
  { title: 'Condizioni particolari', fields: ['exclusivity', 'portfolio', 'insurance', 'other_terms'] },
  { title: 'Rapporti precedenti', fields: ['previous_reports'] },
]

export const LETTER_REQUIRED: ReadonlySet<LetterFieldKey> = new Set<LetterFieldKey>([
  'role',
  'activities',
  'start_date',
  'fee',
  'payment_days',
])
export const LETTER_MULTILINE: ReadonlySet<LetterFieldKey> = new Set<LetterFieldKey>([
  'activities',
  'results',
  'acceptance',
  'purpose_data',
  'other_terms',
])

/** «Come si paga»: one choice that the letter prints twice, as `mode` and as the
 *  fee's `unit` («450,00 € a giornata»), so the page sets both. */
export type PayMode = 'to_work_unit' | 'lump_sum'
/** The words the letter prints after the fee (`unit`): what the core's `PAY_MODE_WORDS` says. */
const PAY_UNIT_WORDS: Record<PayMode, string> = { to_work_unit: 'a giornata', lump_sum: 'a corpo' }

export function payModeOf(form: LetterForm): PayMode {
  return form.mode.trim() === 'lump_sum' ? 'lump_sum' : 'to_work_unit'
}

export function withPayMode(form: LetterForm, mode: PayMode): LetterForm {
  return { ...form, mode, unit: PAY_UNIT_WORDS[mode] }
}

/** «Tipo di lettera» on «Chi e per chi» (REB-687), in the order the page shows them. */
export const REGIME_OPTIONS: readonly { regime: Regime; label: string }[] = [
  { regime: 'vat_registered', label: 'Incarico con partita IVA' },
  { regime: 'occasional', label: 'Prestazione occasionale' },
]

/** The hint under «Compenso (€)»: an ordinary fee is net of VAT, an occasional one gross
 *  and without VAT. */
export function feeHint(regime: Regime): string {
  return regime === 'occasional' ? 'lordo, senza IVA' : 'IVA esclusa'
}

/** The preview links of «Controlla e invia», named by the letter type. */
export function previewLabels(regime: Regime): { letter: string; framework: string } {
  return regime === 'occasional'
    ? { letter: 'Apri la lettera occasionale (PDF)', framework: 'Apri il contratto quadro per prestazioni occasionali (PDF)' }
    : { letter: 'Apri la lettera (PDF)', framework: 'Apri il contratto quadro (PDF)' }
}

const amount = amountNumber

/** «Come si paga» chosen on the page. The prefill's fee is the freelancer's day rate,
 *  never a lump sum: «A corpo» empties a fee still equal to it, so the total is typed,
 *  and «A giornata» puts the day rate back into an empty fee. A fee the admin typed
 *  stays either way. */
export function switchPayMode(form: LetterForm, mode: PayMode, dayRate: string): LetterForm {
  const next = withPayMode(form, mode)
  const fee = form.fee.trim()
  if (!dayRate) return next
  if (mode === 'lump_sum' && fee && amount(fee) === amount(dayRate)) return { ...next, fee: '' }
  if (mode === 'to_work_unit' && !fee) return { ...next, fee: dayRate }
  return next
}

/** What only a fixed-price engagement prints, asked right after the fee «A corpo». */
export const TO_BODY_FIELDS: readonly LetterTextFieldKey[] = ['results', 'acceptance', 'billing_deadlines']

/** What «Condizioni» asks in the open: what changes from one engagement to the next. */
export const TERMS_FIELDS: ReadonlySet<LetterFieldKey> = new Set<LetterFieldKey>([
  'role',
  'activities',
  'start_date',
  'end_date',
  'commitment',
  'location',
  'mode',
  'unit',
  'fee',
  'payment_days',
  'month_end',
  ...TO_BODY_FIELDS,
])

const OTHER_SECTIONS = LETTER_GROUPS.map((group) => ({
  title: group.title,
  fields: group.fields.filter(
    (field): field is LetterTextFieldKey => field !== 'month_end' && !TERMS_FIELDS.has(field),
  ),
}))

/** Every other field of the letter inside the closed «Altre condizioni (facoltative)»,
 *  in the letter's sections and order. A section left with one field would only repeat
 *  its label («Preavviso», «Giorni di preavviso»), so those fields share one last
 *  «Altro». */
export const OTHER_TERMS_GROUPS: readonly { title: string; fields: readonly LetterTextFieldKey[] }[] = [
  ...OTHER_SECTIONS.filter((group) => group.fields.length > 1),
  { title: 'Altro', fields: OTHER_SECTIONS.filter((group) => group.fields.length === 1).flatMap((group) => group.fields) },
].filter((group) => group.fields.length > 0)

export const OTHER_TERMS_FIELDS: ReadonlySet<string> = new Set(
  OTHER_TERMS_GROUPS.flatMap((group) => group.fields),
)

/** The letter as the API takes it. A day rate prints no deliverables, acceptance or
 *  invoice dates, even ones typed «A corpo» before switching back: the form keeps them
 *  for a switch back, the letter does not get them. */
export function letterToSend(form: LetterForm): Letter {
  if (payModeOf(form) === 'lump_sum') return toLetter(form)
  return toLetter({ ...form, ...Object.fromEntries(TO_BODY_FIELDS.map((field) => [field, ''])) })
}

type Named = Pick<ContractDocument, 'kind' | 'number' | 'regime'>

/** How a document is named in a button's label or a confirmation, the one place both
 *  the admin's matches-and-contracts page (REB-407) and the member area's contracts
 *  (REB-392) name a document, as the object of "of": the framework agreement, the
 *  occasional one (REB-687), a numbered letter. Takes just the
 *  fields a label needs, so a `MemberContract` names a document the same way a
 *  `ContractDocument` does. */
export function whatOf(document: Named): string {
  if (document.kind === 'letter') return `della lettera n. ${document.number}`
  return document.regime === 'occasional' ? 'del contratto quadro per prestazioni occasionali' : 'del contratto quadro'
}

/** The same document as the object of a verb (sign, cancel), with its article. */
export function objectOf(document: Named): string {
  if (document.kind === 'letter') return `la lettera n. ${document.number}`
  return document.regime === 'occasional' ? 'il contratto quadro per prestazioni occasionali' : 'il contratto quadro'
}

/** How a match is named in a button's label: its company and its role, since one
 *  company can have a match for each of two roles. */
export function matchOf(match: Pick<Match, 'company_name' | 'requested_role'>): string {
  return `il match con ${match.company_name} come ${match.requested_role}`
}

/** What «Chiudi match» asks before it acts: the engagement ends and the page cannot
 *  reopen it; the letter and the framework agreement are left as they are. */
export function closeDescription(match: Match): string {
  return `Il match con ${match.company_name} come ${match.requested_role} diventa «Concluso»: l’incarico è finito, e dalla pagina non si riapre. La lettera n. ${match.letter.number} e il contratto quadro restano come sono.`
}

/** The headings «Match e contratti» moves the focus to when an action took away the
 *  control that started it: the card's, or the section's when the card went too. A
 *  framework agreement's card has one per regime (REB-687). */
export const FRAMEWORK_HEADING_ID = 'framework-contracts'
export const MATCHES_HEADING_ID = 'match-contracts'
export function frameworkHeadingId(regime: Regime): string {
  return regime === 'occasional' ? `${FRAMEWORK_HEADING_ID}-occasional` : FRAMEWORK_HEADING_ID
}
export function matchHeadingId(matchId: string): string {
  return `match-${matchId}`
}

/** What «Annulla» on a match asks before it acts: the match and its letter's own
 *  number become `cancelled` for good, and, when the letter has already left, that
 *  its envelope on the signing site is cancelled too and the freelancer's link stops
 *  working (REB-407). The framework agreement is the freelancer's, not the match's,
 *  and stays untouched either way. */
export function cancelDescription(match: Match): string {
  const base = `Il match con ${match.company_name} e la lettera n. ${match.letter.number} diventano annullati, e il numero non si riusa. Il contratto quadro resta com’è.`
  return match.letter.status === 'sent'
    ? `${base} La lettera è già partita: viene annullata anche sul sito di firma, e il link ricevuto dal freelance smette di funzionare.`
    : base
}

/** The primary button of «Controlla e invia», with the freelancer's first name: «ad»
 *  before an a, as in «Invia ad Ada per la firma». */
export function sendLabel(firstName: string): string {
  const name = firstName.trim()
  if (!name) return 'Invia per la firma'
  return `Invia ${/^[aàAÀ]/.test(name) ? 'ad' : 'a'} ${name} per la firma`
}

/** What «Match e contratti» says on arrival after «Salva senza inviare». */
export const DRAFT_SAVED = 'Bozza salvata: la trovi qui sotto, da inviare.'

/** A refused request as a wizard shows it: the sentence, the fields to mark, and whether
 *  it is a 409, refused for what the row already holds rather than for what the admin
 *  typed, so the way onward is the row, not a field (REB-406). */
export interface Failure {
  message: string
  fields: string[]
  conflict: boolean
}

export function failureOf(error: unknown, fallback: string): Failure | null {
  if (!error) return null
  if (error instanceof ApiError) return { message: error.message, fields: error.fields, conflict: error.status === 409 }
  return { message: fallback, fields: [], conflict: false }
}

/** The sentence the pages show after «Invia per la firma» (REB-390). */
export function sendReportMessage(report: SendReport): string {
  const number = report.match.letter.number
  const sent =
    report.sent === 'framework'
      ? `Partito il contratto quadro: la lettera n. ${number} partirà da sola dopo la sua firma.`
      : report.sent === 'letter'
        ? `Partita la lettera di incarico n. ${number}.`
        : `La lettera n. ${number} aspetta il contratto quadro già in firma e partirà da sola dopo.`
  return report.mail_sent === false ? `${sent} La mail però non è partita: usa «Reinvia email».` : sent
}
