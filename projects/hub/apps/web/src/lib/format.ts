import type { Action, PigroStato } from './api'

const euro = new Intl.NumberFormat('it-IT', { style: 'currency', currency: 'EUR', useGrouping: 'always' })
const day = new Intl.DateTimeFormat('it-IT', { day: 'numeric', month: 'short', year: 'numeric' })
const moment = new Intl.DateTimeFormat('it-IT', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
})

/** `"450.00"` from the API, as `450,00 €`. Parsed for display only; nothing here is summed. */
export function formatEuro(value: string): string {
  return euro.format(Number(value))
}

export function formatDate(value: string): string {
  return day.format(new Date(value))
}

/** Day and time, for things that happen several times a day: a comment thread. */
export function formatDateTime(value: string): string {
  return moment.format(new Date(value))
}

// A calendar day the API sends as `YYYY-MM-DD` is no instant: read at midnight UTC and
// written in UTC, it stays the same day in every browser's time zone.
const calendarDay = new Intl.DateTimeFormat('it-IT', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })
const calendarWeekday = new Intl.DateTimeFormat('it-IT', {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  timeZone: 'UTC',
})
const calendarMonth = new Intl.DateTimeFormat('it-IT', { month: 'long', year: 'numeric', timeZone: 'UTC' })
const quantity = new Intl.NumberFormat('it-IT', { maximumFractionDigits: 2 })

/** `"2026-10-01"` as `1 ott 2026`, or `gio 1 ott 2026` with its weekday: a row of hours. */
export function formatDay(value: string, weekday = false): string {
  return (weekday ? calendarWeekday : calendarDay).format(new Date(`${value}T00:00:00Z`))
}

/** `"2026-10"` as `Ottobre 2026`, a label on its own. */
export function formatMonth(value: string): string {
  const label = calendarMonth.format(new Date(`${value}-01T00:00:00Z`))
  return label.charAt(0).toUpperCase() + label.slice(1)
}

/** `"7.50"` from the API as `7,5`, `"96.00"` as `96`: hours, days or a percentage, for
 *  display only. Nothing here is summed (`lib/report.ts` sums the strings). */
export function formatDecimal(value: string): string {
  return quantity.format(Number(value))
}

export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

export const REMOTO_LABELS = { remoto: 'Da remoto', ibrido: 'Ibrido', in_sede: 'In sede' } as const
export const FREELANCER_STATES = ['nuovo', 'contattato', 'attivo', 'scartato'] as const
/** The states the freelancer list filters by: the card's four, plus the pseudo-state of a
 *  signup with no card (ORB-163). */
export const FREELANCER_LIST_STATES = [...FREELANCER_STATES, 'lead'] as const
export const COMPANY_STATES = ['nuovo', 'contattato', 'in_corso', 'chiuso'] as const
export const STATE_LABELS: Record<string, string> = {
  nuovo: 'Nuovo',
  lead: 'Lead',
  contattato: 'Contattato',
  attivo: 'Attivo',
  scartato: 'Scartato',
  in_corso: 'In corso',
  chiuso: 'Chiuso',
}

/** REB-413: the state chips on the «Match» list, in the order the brief gives them. */
export const MATCH_STATES = ['bozza', 'in_firma', 'attivo', 'concluso', 'annullato'] as const
/** What a match and a contract document are, in the admin's words: the same as the
 *  core's `MATCH_STATE_LABELS` and `DOCUMENT_STATE_LABELS` (`match_words.py`), which the
 *  MCP tools answer, so keep the two identical (REB-477): the core's
 *  `tests/test_web_labels.py` names any label that drifts. */
export const MATCH_STATE_LABELS: Record<string, string> = {
  bozza: 'Da inviare',
  in_firma: 'In attesa di firma',
  attivo: 'Attivo',
  concluso: 'Concluso',
  annullato: 'Annullato',
}
export const DOCUMENT_STATE_LABELS: Record<string, string> = {
  generato: 'Pronto, non inviato',
  in_attesa: 'Parte dopo il contratto quadro',
  inviato: 'Da firmare',
  firmato: 'Firmato',
  annullato: 'Annullato',
  disdetto: 'Disdetto',
}

/** Where a match's link to its deal on Pigro stands (REB-497), as the «Pigro» column on
 *  the «Match» list reads it. The web's own words: the core writes the sentence, not a
 *  label. */
export const PIGRO_STATE_LABELS: Record<PigroStato, string> = {
  collegato: 'Collegato',
  da_collegare: 'Da collegare',
  errore: 'Errore',
  rifiutato: 'Rifiutato',
}

/** An invoice the hours of «Consuntivo» sit on (REB-503), in the words PigroCRM's own
 *  invoice list uses for its state and its payment: the CRM's vocabulary, copied, since
 *  the hub imports nothing of it. */
export const INVOICE_STATE_LABELS: Record<string, string> = {
  bozza: 'Bozza',
  emessa: 'Emessa',
  annullata: 'Annullata',
  confermata: 'Confermata',
  consumata: 'Consumata',
}
export const PAYMENT_STATE_LABELS: Record<string, string> = {
  da_incassare: 'Da incassare',
  incassato: 'Incassato',
}

/** The words on the button for each step the core names (REB-477). */
export const ACTION_LABELS: Record<Action, string> = {
  invia: 'Invia per la firma',
  reinvia_email: 'Reinvia email',
  aggiorna_stato: 'Aggiorna stato',
  annulla: 'Annulla',
  chiudi: 'Chiudi match',
  registra_disdetta: 'Registra disdetta',
  riprova_pigro: 'Riprova su Pigro',
}
/** The same button while its request runs. */
export const ACTION_PENDING_LABELS: Record<Action, string> = {
  invia: 'Invio…',
  reinvia_email: 'Reinvio…',
  aggiorna_stato: 'Aggiorno…',
  annulla: 'Annullo…',
  chiudi: 'Chiudo…',
  registra_disdetta: 'Registro…',
  riprova_pigro: 'Collego a Pigro…',
}

/** REB-392: a contract's state in the freelancer's own words, gendered to the document:
 *  the framework agreement (`il contratto quadro`) is masculine, a letter (`la lettera`)
 *  feminine. `inviato` keeps «Da firmare» for both: gender-neutral, and it tells the
 *  member what to do rather than just naming the state. `disdetto`/`disdetta` never
 *  actually reaches a letter (only a framework agreement can be,
 *  `ck_contract_documents_notice_for_quadro`), kept here only so a stray value still
 *  reads as a sentence rather than the raw state. */
export const MEMBER_FRAMEWORK_STATE_LABELS: Record<string, string> = {
  in_attesa: 'Parte dopo la firma del contratto quadro',
  inviato: 'Da firmare',
  firmato: 'Firmato',
  annullato: 'Annullato: non va più firmato',
  disdetto: 'Disdetto',
}
export const MEMBER_LETTER_STATE_LABELS: Record<string, string> = {
  in_attesa: 'Parte dopo la firma del contratto quadro',
  inviato: 'Da firmare',
  firmato: 'Firmata',
  annullato: 'Annullata: non va più firmata',
  disdetto: 'Disdetta',
}

/** Which of the two gendered maps a document's own words come from. */
export function memberDocumentStateLabel(kind: 'quadro' | 'lettera', stato: string): string {
  const labels = kind === 'quadro' ? MEMBER_FRAMEWORK_STATE_LABELS : MEMBER_LETTER_STATE_LABELS
  return labels[stato] ?? stato
}
