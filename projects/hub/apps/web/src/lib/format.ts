import type { Action } from './api'

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

/** The words on the button for each step the core names (REB-477). */
export const ACTION_LABELS: Record<Action, string> = {
  invia: 'Invia per la firma',
  reinvia_email: 'Reinvia email',
  aggiorna_stato: 'Aggiorna stato',
  annulla: 'Annulla',
  chiudi: 'Chiudi match',
  registra_disdetta: 'Registra disdetta',
}
/** The same button while its request runs. */
export const ACTION_PENDING_LABELS: Record<Action, string> = {
  invia: 'Invio…',
  reinvia_email: 'Reinvio…',
  aggiorna_stato: 'Aggiorno…',
  annulla: 'Annullo…',
  chiudi: 'Chiudo…',
  registra_disdetta: 'Registro…',
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
