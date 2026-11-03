import type { Action, PigroStatus } from './api'

const day = new Intl.DateTimeFormat('it-IT', { day: 'numeric', month: 'short', year: 'numeric' })
const moment = new Intl.DateTimeFormat('it-IT', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
})

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

/** A rate as the API sends it, `"0.1000"`, as `10%`: what a referral reward is a share of. */
export function formatRate(value: string): string {
  return `${quantity.format(Number(value) * 100)}%`
}

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

export const RemoteLabels = { remote: 'Da remoto', hybrid: 'Ibrido', on_site: 'In sede' } as const
export const FREELANCER_STATES = ['new', 'contacted', 'active', 'discarded'] as const
/** The states the freelancer list filters by: the card's four, plus the pseudo-state of a
 *  signup with no card (ORB-163). */
export const FREELANCER_LIST_STATES = [...FREELANCER_STATES, 'lead'] as const
export const COMPANY_STATES = ['new', 'contacted', 'in_progress', 'closed'] as const
export const STATE_LABELS: Record<string, string> = {
  new: 'Nuovo',
  lead: 'Lead',
  contacted: 'Contattato',
  active: 'Attivo',
  discarded: 'Scartato',
  in_progress: 'In corso',
  closed: 'Chiuso',
}

/** REB-413: the state chips on the «Match» list, in the order the brief gives them. */
export const MATCH_STATES = ['draft', 'awaiting_signature', 'active', 'concluded', 'cancelled'] as const
/** What a match and a contract document are, in the admin's words: the same as the
 *  core's `MATCH_STATE_LABELS` and `DOCUMENT_STATE_LABELS` (`match_words.py`), which the
 *  MCP tools answer, so keep the two identical (REB-477): the core's
 *  `tests/test_web_labels.py` names any label that drifts. */
export const MATCH_STATE_LABELS: Record<string, string> = {
  draft: 'Da inviare',
  awaiting_signature: 'In attesa di firma',
  active: 'Attivo',
  concluded: 'Concluso',
  cancelled: 'Annullato',
}
export const DOCUMENT_STATE_LABELS: Record<string, string> = {
  generated: 'Pronto, non inviato',
  pending: 'Parte dopo il contratto quadro',
  sent: 'Da firmare',
  signed: 'Firmato',
  cancelled: 'Annullato',
  notice_served: 'Disdetto',
}

/** P-REB-43: a team request's states, in the order the admin moves it («Richieste
 *  team»), and its words, where it came from, and a talent's answer to the availability
 *  mail: copies of the core's `team_words.py`, held equal by the core's
 *  `tests/test_web_labels.py` as the match labels above are. */
export const TEAM_REQUEST_STATES = ['new', 'contacted', 'closed'] as const
export const TEAM_REQUEST_STATE_LABELS: Record<string, string> = {
  new: 'Nuova',
  contacted: 'Contattata',
  closed: 'Chiusa',
}
export const TEAM_ORIGIN_LABELS: Record<string, string> = {
  public: 'Pubblico',
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

/** A team member's `week_days` as the team reads it: «1 giorno a settimana»,
 *  «3 giorni a settimana», or «Giorni da definire» when the proposal left it open. */
export function formatDaysPerWeek(days: number | null): string {
  if (days === null) return 'Giorni da definire'
  return days === 1 ? '1 giorno a settimana' : `${days} giorni a settimana`
}

/** A card's `experience_years`, as a phrase: «meno di un anno», «1 anno», «9 anni di esperienza»,
 *  and «Anni non indicati nel CV» when the CV dates no role (REB-799). */
export function formatExperience(experience_years: number | null): string {
  if (experience_years === null) return 'Anni non indicati nel CV'
  if (experience_years === 0) return 'meno di un anno di esperienza'
  return experience_years === 1 ? '1 anno di esperienza' : `${experience_years} anni di esperienza`
}

/** A card's level for a company: «Senior, 9 anni di esperienza», with «(da verificare)»
 *  after a seniority the CV's dates could not back (REB-799). */
export function formatLevel(card: {
  seniority: string
  experience_years: number | null
  seniority_uncertain?: boolean
}): string {
  const seniority = SENIORITY_LABELS[card.seniority] ?? card.seniority
  return `${seniority}${card.seniority_uncertain ? ' (da verificare)' : ''}, ${formatExperience(card.experience_years)}`
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

/** Where a match's link to its deal on Pigro stands (REB-497), as the «Pigro» column on
 *  the «Match» list reads it. The web's own words: the core writes the sentence, not a
 *  label. */
export const PIGRO_STATE_LABELS: Record<PigroStatus, string> = {
  linked: 'Collegato',
  to_link: 'Da collegare',
  error: 'Errore',
  rejected: 'Rifiutato',
}

/** A referral's state on the «Match» list and on the ledger: the reward's own three (the
 *  ledger's `STATE_LABELS` read the same), `previsto` while its first letter is not signed
 *  (an estimate), `already_accrued` when it already paid on another match (REB-609) and
 *  `to_verify` while the referred person has not proved they hold the address (REB-658). */
export const REFERRAL_STATE_LABELS: Record<string, string> = {
  expected: 'Previsto',
  to_verify: 'Da verificare',
  to_confirm: 'Da confermare',
  confirmed: 'Confermato',
  paid: 'Pagato',
  already_accrued: 'Già maturato',
}
/** What verified a referral (REB-658), as the ledger row says it, one whole sentence each. */
export const REFERRAL_VERIFIED_VIA_LABELS: Record<string, string> = {
  access: 'Verificato con il primo accesso',
  letter: 'Verificato con la lettera firmata',
  history: 'Già contava prima della verifica',
}
/** Which side of a match a referral brought in. */
export const REFERRAL_KIND_LABELS: Record<string, string> = { freelancer: 'freelance', company: 'company' }

/** An invoice the hours of «Consuntivo» sit on (REB-503), in the words PigroCRM's own
 *  invoice list uses for its state and its payment: the CRM's vocabulary, copied, since
 *  the hub imports nothing of it. */
export const INVOICE_STATE_LABELS: Record<string, string> = {
  draft: 'Bozza',
  issued: 'Emessa',
  cancelled: 'Annullata',
  confirmed: 'Confermata',
  consumed: 'Consumata',
}
export const PAYMENT_STATE_LABELS: Record<string, string> = {
  from_unpaid: 'Da incassare',
  paid: 'Incassato',
}

/** The words on the button for each step the core names (REB-477). */
export const ACTION_LABELS: Record<Action, string> = {
  send: 'Invia per la firma',
  resend_email: 'Reinvia email',
  status_refresh: 'Aggiorna stato',
  cancel: 'Annulla',
  close: 'Chiudi match',
  record_notice: 'Registra disdetta',
  retry_pigro: 'Riprova su Pigro',
}
/** The same button while its request runs. */
export const ACTION_PENDING_LABELS: Record<Action, string> = {
  send: 'Invio…',
  resend_email: 'Reinvio…',
  status_refresh: 'Aggiorno…',
  cancel: 'Annullo…',
  close: 'Chiudo…',
  record_notice: 'Registro…',
  retry_pigro: 'Collego a Pigro…',
}

/** REB-392: a contract's state in the freelancer's own words, gendered to the document:
 *  the framework agreement (`il contratto quadro`) is masculine, a letter (`la lettera`)
 *  feminine. `sent` keeps «Da firmare» for both: gender-neutral, and it tells the
 *  member what to do rather than just naming the state. `notice_served`/`disdetta` never
 *  actually reaches a letter (only a framework agreement can be,
 *  `ck_contract_documents_notice_for_framework`), kept here only so a stray value still
 *  reads as a sentence rather than the raw state. */
export const MEMBER_FRAMEWORK_STATE_LABELS: Record<string, string> = {
  pending: 'Parte dopo la firma del contratto quadro',
  sent: 'Da firmare',
  signed: 'Firmato',
  cancelled: 'Annullato: non va più firmato',
  notice_served: 'Disdetto',
}
export const MEMBER_LETTER_STATE_LABELS: Record<string, string> = {
  pending: 'Parte dopo la firma del contratto quadro',
  sent: 'Da firmare',
  signed: 'Firmata',
  cancelled: 'Annullata: non va più firmata',
  notice_served: 'Disdetta',
}

/** Which of the two gendered maps a document's own words come from. */
export function memberDocumentStateLabel(kind: 'framework' | 'letter', status: string): string {
  const labels = kind === 'framework' ? MEMBER_FRAMEWORK_STATE_LABELS : MEMBER_LETTER_STATE_LABELS
  return labels[status] ?? status
}

// A model call costs a fraction of a cent: `formatMoney`'s two places would show it as 0,00 €.
const smallEuro = new Intl.NumberFormat('it-IT', {
  style: 'currency',
  currency: 'EUR',
  minimumFractionDigits: 2,
  maximumFractionDigits: 6,
})

/** `"0.012300"` from the API as `0,0123 €`: what a call or a regeneration cost. */
export function formatSmallEuro(value: string): string {
  return smallEuro.format(value as `${number}`)
}
