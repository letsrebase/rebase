/**
 * The drafts the matches pages edit (REB-387): an input always holds a string, the API
 * takes typed values, and these helpers are the one place the two meet. Kept out of the
 * page files, which export components only (`react-refresh/only-export-components`).
 */
import {
  ApiError,
  LETTERA_TEXT_KEYS,
  type Cliente,
  type ClienteDraft,
  type Company,
  type ContractDocument,
  type Fiscal,
  type FiscalData,
  type Lettera,
  type LetteraDraft,
  type LetteraTextKey,
  type Match,
  type SendReport,
} from './api'
import { formatDate } from './format'

export type FiscalDraft = Record<keyof FiscalData, string>

export const FISCAL_EMPTY: FiscalDraft = { codice_fiscale: '', partita_iva: '', domicilio: '', pec: '' }

export function draftFromFiscal(fiscal: Fiscal | null): FiscalDraft {
  if (fiscal === null) return FISCAL_EMPTY
  return {
    codice_fiscale: fiscal.codice_fiscale,
    partita_iva: fiscal.partita_iva,
    domicilio: fiscal.domicilio,
    pec: fiscal.pec ?? '',
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

/** The typed fields a save did not cover, because their text changed after it was sent
 *  (compared as sent, trimmed): once the save succeeds only these stay typed, and the
 *  others take the record that comes back. */
export function typedAfterSave(draft: FiscalDraft, typed: ReadonlySet<FiscalKey>, sent: FiscalData): Set<FiscalKey> {
  const now = toFiscalData(draft)
  return new Set([...typed].filter((key) => now[key] !== sent[key]))
}

/** An empty PEC is `null`, not `""`, which the API would try to read as an address. */
export function toFiscalData(draft: FiscalDraft): FiscalData {
  return {
    codice_fiscale: draft.codice_fiscale.trim(),
    partita_iva: draft.partita_iva.trim(),
    domicilio: draft.domicilio.trim(),
    pec: draft.pec.trim() || null,
  }
}

/** Saved tax data in one line: «Chi e per chi» shows it instead of their four fields,
 *  and «Dati fiscali» on «Match e contratti» in its closed summary. */
export function fiscalLine(fiscal: FiscalData): string {
  return `Salvati: CF ${fiscal.codice_fiscale} · P.IVA ${fiscal.partita_iva}`
}

/** What «Avanti» on «Chi e per chi» saves: the four fields when nothing was saved yet,
 *  or when the admin opened the saved ones and changed them; `null` otherwise, so data
 *  only shown are never written again under this admin's name. */
export function fiscalToSave(saved: FiscalData | null, draft: FiscalDraft, editing: boolean): FiscalData | null {
  const data = toFiscalData(draft)
  if (saved === null) return data
  if (!editing) return null
  const same = (Object.keys(data) as (keyof FiscalData)[]).every((key) => (saved[key] ?? null) === data[key])
  return same ? null : data
}

export type ClienteForm = Record<keyof Cliente, string>
export const CLIENTE_EMPTY: ClienteForm = { cliente_ragione_sociale: '', cliente_piva: '', cliente_sede: '' }

export function clienteForm(draft: ClienteDraft): ClienteForm {
  return {
    cliente_ragione_sociale: draft.cliente_ragione_sociale ?? '',
    cliente_piva: draft.cliente_piva ?? '',
    cliente_sede: draft.cliente_sede ?? '',
  }
}

export function toCliente(form: ClienteForm): Cliente {
  return {
    cliente_ragione_sociale: form.cliente_ragione_sociale.trim(),
    cliente_piva: form.cliente_piva.trim(),
    cliente_sede: form.cliente_sede.trim(),
  }
}

/** The request «Chi e per chi» folds its list into once one is picked. Never the
 *  budget, which this flow does not show (spec § 1h). */
export function requestLine(company: Company): string {
  return `${company.nome_azienda} · ${company.referente} · ${company.figura_richiesta} · dal ${formatDate(company.periodo_da)}`
}

/** What a refusal can name that «Chi e per chi» asks: the client on the letter, and the
 *  freelancer's tax data (`fiscale` when they are missing altogether). */
export const CLIENTE_FIELDS: ReadonlySet<string> = new Set<keyof Cliente>([
  'cliente_ragione_sociale',
  'cliente_piva',
  'cliente_sede',
])
export const FISCAL_FIELDS: ReadonlySet<string> = new Set(['fiscale', ...Object.keys(FISCAL_EMPTY)])

/** A client with all three details reads as one line; one missing asks for the fields. */
export function clienteComplete(form: ClienteForm): boolean {
  return Object.values(toCliente(form)).every(Boolean)
}

export function clienteLine(form: ClienteForm): string {
  const cliente = toCliente(form)
  return `${cliente.cliente_ragione_sociale} · P.IVA ${cliente.cliente_piva} · ${cliente.cliente_sede}`
}

export type LetteraForm = Record<LetteraTextKey, string> & {
  data_inizio: string
  data_fine: string
  compenso: string
  giorni_pagamento: string
  fine_mese: boolean
  giorni_preavviso: string
}
export type LetteraFieldKey = keyof LetteraForm
/** A field an input or a textarea holds: every one but `fine_mese`, the checkbox. */
export type LetteraTextFieldKey = Exclude<LetteraFieldKey, 'fine_mese'>

const TEXT_EMPTY = Object.fromEntries(LETTERA_TEXT_KEYS.map((key) => [key, ''])) as Record<LetteraTextKey, string>

export const LETTERA_EMPTY: LetteraForm = {
  ...TEXT_EMPTY,
  data_inizio: '',
  data_fine: '',
  compenso: '',
  giorni_pagamento: '',
  fine_mese: false,
  giorni_preavviso: '',
}

/** A fee as an Italian types it, in the machine form the API takes. With a comma, the
 *  comma is the decimal and every dot a thousands separator («1.234,50» is 1234.50).
 *  Without one, dots that group the digits in threes are thousands too («12.000» is
 *  12000), while a single dot before one or two digits is already the machine form
 *  («480.50»). Anything else goes as typed, for the API to refuse by its field. */
export function machineAmount(value: string): string {
  const text = value.trim()
  if (text.includes(',')) return text.replaceAll('.', '').replace(',', '.')
  if (/^\d{1,3}(\.\d{3})+$/.test(text)) return text.replaceAll('.', '')
  return text
}

/** The API's amount as the fee field shows it, the way the letter writes it: «480» for
 *  «480.00», «480,50» for «480.50». `toLettera` reads the comma back. */
export function amountForm(value: string): string {
  const [whole = '', cents = ''] = value.trim().split('.')
  const significant = cents.replace(/0+$/, '')
  return significant ? `${whole},${significant.padEnd(2, '0')}` : whole
}

export function letteraForm(draft: LetteraDraft): LetteraForm {
  const text = Object.fromEntries(LETTERA_TEXT_KEYS.map((key) => [key, draft[key] ?? ''])) as Record<LetteraTextKey, string>
  return {
    ...text,
    data_inizio: draft.data_inizio ?? '',
    data_fine: draft.data_fine ?? '',
    compenso: draft.compenso === null ? '' : amountForm(draft.compenso),
    giorni_pagamento: draft.giorni_pagamento === null ? '' : String(draft.giorni_pagamento),
    fine_mese: draft.fine_mese ?? false,
    giorni_preavviso: draft.giorni_preavviso === null ? '' : String(draft.giorni_preavviso),
  }
}

export function toLettera(form: LetteraForm): Lettera {
  const text = Object.fromEntries(LETTERA_TEXT_KEYS.map((key) => [key, form[key].trim() || null])) as Record<
    LetteraTextKey,
    string | null
  >
  return {
    ...text,
    data_inizio: form.data_inizio,
    data_fine: form.data_fine || null,
    compenso: machineAmount(form.compenso),
    giorni_pagamento: Number(form.giorni_pagamento),
    fine_mese: form.fine_mese,
    giorni_preavviso: form.giorni_preavviso.trim() ? Number(form.giorni_preavviso) : null,
  }
}

/** «Giorni previsti» (REB-497), the admin's estimate of the engagement's billable days,
 *  as `MatchCreate` takes it: a number, or `null` for an empty box (and for text that is
 *  no number, which JSON would send as `null` anyway). A fraction or a number out of range
 *  goes as typed, for the API to refuse by its field. The wizard keeps the box beside
 *  `LetteraForm`, never in it, so `toLettera` cannot send it: `LetteraFields` refuses a
 *  key it does not know, and nothing in the letter changes with this number. */
export function giorniPrevistiToSend(text: string): number | null {
  const value = text.trim()
  const days = Number(value)
  return value && Number.isFinite(days) ? days : null
}

/** The letter's fields as «Crea match» names them. The client's budget has no label
 *  here, because it has no field anywhere in this flow (spec § 1h). The page asks
 *  `modalita` and `unita` as one choice, «Come si paga», and `giorni_pagamento` inside
 *  its sentence, «Pagamento a … giorni», so those three labels show nowhere on it. */
export const LETTERA_LABELS: Record<LetteraFieldKey, string> = {
  ruolo: 'Ruolo',
  attivita: 'Cosa farà',
  risultati: 'Risultati da consegnare',
  accettazione: 'Come il cliente accetta i risultati',
  data_inizio: 'Inizio',
  data_fine: 'Fine prevista (facoltativa)',
  impegno: 'Impegno',
  periodo_verifica: 'Periodo iniziale di verifica',
  luogo: 'Dove',
  coordinamento: 'Coordinamento concordato con il cliente',
  referente_cliente: 'Referente del cliente',
  referente_rebase: 'Referente di rebase',
  modalita: 'Modalità',
  compenso: 'Compenso, IVA esclusa (€)',
  unita: 'Unità del compenso',
  lavoro_extra: 'Lavoro festivo o fuori fascia',
  spese: 'Spese',
  giorni_pagamento: 'Giorni di pagamento',
  fine_mese: 'fine mese',
  scadenze_fatturazione: 'Scadenze di fatturazione',
  giorni_preavviso: 'Giorni di preavviso',
  dati_personali: 'Tratta dati personali del cliente',
  dati_finalita: 'Natura e finalità del trattamento',
  dati_categorie: 'Categorie di dati',
  dati_interessati: 'Categorie di interessati',
  dati_autorizzazione: 'Autorizzazione scritta del cliente alla nomina',
  esclusiva: 'Esclusiva verso il cliente',
  portfolio: 'Citazione nel portfolio',
  assicurazione: 'Assicurazione di responsabilità civile professionale',
  altre_condizioni: 'Altre condizioni',
  rapporti_precedenti: 'Rapporti precedenti con il cliente',
}

/** The letter's own sections, the order «Altre condizioni» keeps. */
export const LETTERA_GROUPS: readonly { title: string; fields: readonly LetteraFieldKey[] }[] = [
  { title: 'Attività', fields: ['ruolo', 'attivita', 'risultati', 'accettazione'] },
  { title: 'Tempi e impegno', fields: ['data_inizio', 'data_fine', 'impegno', 'periodo_verifica'] },
  { title: 'Modalità di lavoro', fields: ['luogo', 'coordinamento', 'referente_cliente', 'referente_rebase'] },
  {
    title: 'Condizioni economiche',
    fields: ['modalita', 'compenso', 'unita', 'lavoro_extra', 'spese', 'giorni_pagamento', 'fine_mese', 'scadenze_fatturazione'],
  },
  { title: 'Preavviso', fields: ['giorni_preavviso'] },
  {
    title: 'Dati personali',
    fields: ['dati_personali', 'dati_finalita', 'dati_categorie', 'dati_interessati', 'dati_autorizzazione'],
  },
  { title: 'Condizioni particolari', fields: ['esclusiva', 'portfolio', 'assicurazione', 'altre_condizioni'] },
  { title: 'Rapporti precedenti', fields: ['rapporti_precedenti'] },
]

export const LETTERA_REQUIRED: ReadonlySet<LetteraFieldKey> = new Set<LetteraFieldKey>([
  'ruolo',
  'attivita',
  'data_inizio',
  'compenso',
  'giorni_pagamento',
])
export const LETTERA_MULTILINE: ReadonlySet<LetteraFieldKey> = new Set<LetteraFieldKey>([
  'attivita',
  'risultati',
  'accettazione',
  'dati_finalita',
  'altre_condizioni',
])

/** «Come si paga»: one choice that the letter prints twice, as `modalita` and as the
 *  fee's `unita` («450,00 € a giornata»), so the page sets both. */
export type PayMode = 'a giornata' | 'a corpo'

export function payModeOf(form: LetteraForm): PayMode {
  return form.modalita.trim() === 'a corpo' ? 'a corpo' : 'a giornata'
}

export function withPayMode(form: LetteraForm, mode: PayMode): LetteraForm {
  return { ...form, modalita: mode, unita: mode }
}

const amount = (value: string) => Number(machineAmount(value))

/** «Come si paga» chosen on the page. The prefill's fee is the freelancer's day rate,
 *  never a lump sum: «A corpo» empties a fee still equal to it, so the total is typed,
 *  and «A giornata» puts the day rate back into an empty fee. A fee the admin typed
 *  stays either way. */
export function switchPayMode(form: LetteraForm, mode: PayMode, dayRate: string): LetteraForm {
  const next = withPayMode(form, mode)
  const fee = form.compenso.trim()
  if (!dayRate) return next
  if (mode === 'a corpo' && fee && amount(fee) === amount(dayRate)) return { ...next, compenso: '' }
  if (mode === 'a giornata' && !fee) return { ...next, compenso: dayRate }
  return next
}

/** What only a fixed-price engagement prints, asked right after the fee «A corpo». */
export const A_CORPO_FIELDS: readonly LetteraTextFieldKey[] = ['risultati', 'accettazione', 'scadenze_fatturazione']

/** What «Condizioni» asks in the open: what changes from one engagement to the next. */
export const CONDIZIONI_FIELDS: ReadonlySet<LetteraFieldKey> = new Set<LetteraFieldKey>([
  'ruolo',
  'attivita',
  'data_inizio',
  'data_fine',
  'impegno',
  'luogo',
  'modalita',
  'unita',
  'compenso',
  'giorni_pagamento',
  'fine_mese',
  ...A_CORPO_FIELDS,
])

const ALTRE_SECTIONS = LETTERA_GROUPS.map((group) => ({
  title: group.title,
  fields: group.fields.filter(
    (field): field is LetteraTextFieldKey => field !== 'fine_mese' && !CONDIZIONI_FIELDS.has(field),
  ),
}))

/** Every other field of the letter inside the closed «Altre condizioni (facoltative)»,
 *  in the letter's sections and order. A section left with one field would only repeat
 *  its label («Preavviso», «Giorni di preavviso»), so those fields share one last
 *  «Altro». */
export const ALTRE_CONDIZIONI_GROUPS: readonly { title: string; fields: readonly LetteraTextFieldKey[] }[] = [
  ...ALTRE_SECTIONS.filter((group) => group.fields.length > 1),
  { title: 'Altro', fields: ALTRE_SECTIONS.filter((group) => group.fields.length === 1).flatMap((group) => group.fields) },
].filter((group) => group.fields.length > 0)

export const ALTRE_CONDIZIONI_FIELDS: ReadonlySet<string> = new Set(
  ALTRE_CONDIZIONI_GROUPS.flatMap((group) => group.fields),
)

/** The letter as the API takes it. A day rate prints no deliverables, acceptance or
 *  invoice dates, even ones typed «A corpo» before switching back: the form keeps them
 *  for a switch back, the letter does not get them. */
export function letteraToSend(form: LetteraForm): Lettera {
  if (payModeOf(form) === 'a corpo') return toLettera(form)
  return toLettera({ ...form, ...Object.fromEntries(A_CORPO_FIELDS.map((field) => [field, ''])) })
}

/** How a document is named in a button's label or a confirmation, the one place both
 *  the admin's «Match e contratti» (REB-407) and the member area's «Contratti»
 *  (REB-392) name a document: «del contratto quadro», «della lettera n. 2026-001».
 *  Takes just the two fields a label needs, so a `MemberContract` names a document the
 *  same way a `ContractDocument` does. */
export function whatOf(document: Pick<ContractDocument, 'kind' | 'numero'>): string {
  return document.kind === 'quadro' ? 'del contratto quadro' : `della lettera n. ${document.numero}`
}

/** How a match is named in a button's label: its company and its role, since one
 *  company can have a match for each of two roles. */
export function matchOf(match: Pick<Match, 'nome_azienda' | 'figura_richiesta'>): string {
  return `il match con ${match.nome_azienda} come ${match.figura_richiesta}`
}

/** What «Chiudi match» asks before it acts: the engagement ends and the page cannot
 *  reopen it; the letter and the framework agreement are left as they are. */
export function closeDescription(match: Match): string {
  return `Il match con ${match.nome_azienda} come ${match.figura_richiesta} diventa «Concluso»: l’incarico è finito, e dalla pagina non si riapre. La lettera n. ${match.lettera.numero} e il contratto quadro restano come sono.`
}

/** The headings «Match e contratti» moves the focus to when an action took away the
 *  control that started it: the card's, or the section's when the card went too. */
export const QUADRO_HEADING_ID = 'contratti-quadro'
export const MATCHES_HEADING_ID = 'contratti-match'
export function matchHeadingId(matchId: string): string {
  return `match-${matchId}`
}

/** What «Annulla» on a match asks before it acts: the match and its letter's own
 *  number become `annullato` for good, and, when the letter has already left, that
 *  its envelope on the signing site is cancelled too and the freelancer's link stops
 *  working (REB-407). The framework agreement is the freelancer's, not the match's,
 *  and stays untouched either way. */
export function cancelDescription(match: Match): string {
  const base = `Il match con ${match.nome_azienda} e la lettera n. ${match.lettera.numero} diventano annullati, e il numero non si riusa. Il contratto quadro resta com’è.`
  return match.lettera.stato === 'inviato'
    ? `${base} La lettera è già partita: viene annullata anche sul sito di firma, e il link ricevuto dal freelance smette di funzionare.`
    : base
}

/** The primary button of «Controlla e invia», with the freelancer's first name: «ad»
 *  before an a, as in «Invia ad Ada per la firma». */
export function sendLabel(nome: string): string {
  const name = nome.trim()
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
  const numero = report.match.lettera.numero
  const sent =
    report.inviato === 'quadro'
      ? `Partito il contratto quadro: la lettera n. ${numero} partirà da sola dopo la sua firma.`
      : report.inviato === 'lettera'
        ? `Partita la lettera di incarico n. ${numero}.`
        : `La lettera n. ${numero} aspetta il contratto quadro già in firma e partirà da sola dopo.`
  return report.mail_inviata === false ? `${sent} La mail però non è partita: usa «Reinvia email».` : sent
}
