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

/** P-REB-43: a team request's states, in the order the admin moves it («Richieste
 *  team»), and its words, where it came from, and a talent's answer to the availability
 *  mail: copies of the core's `team_words.py`, held equal by the core's
 *  `tests/test_web_labels.py` as the match labels above are. */
export const TEAM_REQUEST_STATES = ['nuova', 'contattata', 'chiusa'] as const
export const TEAM_REQUEST_STATE_LABELS: Record<string, string> = {
  nuova: 'Nuova',
  contattata: 'Contattata',
  chiusa: 'Chiusa',
}
export const TEAM_ORIGIN_LABELS: Record<string, string> = {
  pubblico: 'Pubblico',
  cloud: 'Cloud',
  admin: 'Admin',
}
export const TALENT_ANSWER_LABELS: Record<string, string> = {
  si: 'Sì',
  no: 'No',
}
/** A talent who has the availability mail and has not answered (D1): core's
 *  `team_words.TALENT_WAITING_LABEL`, held equal by `tests/test_web_labels.py`. */
export const TALENT_WAITING_LABEL = 'In attesa'

/** A card's `seniority` (spec § 2.1), in the words a profile uses: the keys are core's
 *  `CARD_SENIORITIES`, which `tests/test_web_labels.py` holds this map to. */
export const SENIORITY_LABELS: Record<string, string> = {
  junior: 'Junior',
  mid: 'Mid',
  senior: 'Senior',
  lead: 'Lead',
}

/** A card's `anni`, as a phrase: «meno di un anno», «1 anno», «9 anni di esperienza». */
export function formatExperience(anni: number): string {
  if (anni === 0) return 'meno di un anno di esperienza'
  return anni === 1 ? '1 anno di esperienza' : `${anni} anni di esperienza`
}

/** REB-518: the talent's «Verificato» pill, core's `team_words.VETTED_LABEL`, and the
 *  two actions of the row's menu that put it on and take it off. */
export const VETTED_LABEL = 'Verificato'
export const VETTED_MARK_LABEL = 'Segna come verificato'
export const VETTED_UNMARK_LABEL = 'Togli la verifica'
/** REB-519: the badge a vetted talent's card carries in the talent cloud, core's
 *  `team_words.CLOUD_VETTED_LABEL`, held equal by `tests/test_web_labels.py`. */
export const CLOUD_VETTED_LABEL = 'Verificato da rebase'
/** A company request that came from the team builder's beta box (`?da=team-builder`,
 *  spec § 4.3), and the word its row in «Aziende» shows: core's `team_words`
 *  `TEAM_BUILDER_ORIGIN` and `TEAM_BUILDER_ORIGIN_LABEL`, held equal by
 *  `tests/test_web_labels.py`. */
export const TEAM_BUILDER_ORIGIN = 'team-builder'
export const TEAM_BUILDER_ORIGIN_LABEL = 'da team builder'

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
