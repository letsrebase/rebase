import type { Campaign, CampaignAzione, CampaignCounts, CampaignMeta, CampaignStato, RecipientStato } from './api'

export const CAMPAIGN_STATE_LABELS: Record<CampaignStato, string> = {
  bozza: 'Bozza',
  programmata: 'Programmata',
  in_invio: 'In invio',
  inviata: 'Inviata',
  annullata: 'Annullata',
}

export const AZIONE_LABELS: Record<CampaignAzione, string> = {
  entrato: 'È entrato nell’area',
  cv: 'Ha caricato il CV',
  scheda_completa: 'Ha completato la scheda',
  profilo_creato: 'Ha creato il profilo',
  richiesta_aggiornata: 'Ha aggiornato la richiesta',
  pigro_cliente: 'Primo cliente in Pigro',
}

/** Phase 1 offers three: the Pigro button arrives with phase 3. */
export const META_LABELS: Partial<Record<CampaignMeta, string>> = {
  area: 'La sua area',
  wizard: 'Il wizard del profilo',
  richiesta: 'La richiesta dell’azienda',
}

export const RECIPIENT_STATE_LABELS: Record<RecipientStato, string> = {
  in_coda: 'In coda',
  inviata: 'Inviata',
  saltata: 'Saltata',
  fallita: 'Fallita',
}

/** The server's rule (`campaigns/render.py:personalise`), for the preview on screen. */
export function personalise(testo: string, nome: string | null): string {
  if (nome) return testo.replaceAll('{nome}', nome)
  return testo.replaceAll(' {nome}', '').replaceAll('{nome}', '')
}

/** The server's `one_line` (`campaigns/render.py`): each run of control characters,
 *  C0 and C1 alike, and the two Unicode line separators becomes one space, and the
 *  ends are trimmed, so a subject can never carry a line break. */
export function oneLine(text: string): string {
  return text.replace(/[\p{Cc}\u2028\u2029]+/gu, ' ').trim()
}

/** The subject a person receives (`campaigns/render.py:render`): the name on one line,
 *  a name that is nothing once trimmed counting as missing, then the whole subject on
 *  one line too. */
export function subjectFor(oggetto: string, nome: string | null): string {
  return oneLine(personalise(oggetto, oneLine(nome ?? '') || null))
}

/** Today and the current time in Europe/Rome, as the «Programma» inputs want them. */
export function romeToday(now = new Date()): { giorno: string; ora: string } {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Europe/Rome',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    })
      .formatToParts(now)
      .map((part) => [part.type, part.value]),
  )
  return { giorno: `${parts.year}-${parts.month}-${parts.day}`, ora: `${parts.hour}:${parts.minute}` }
}

const QUARTER_HOUR_MS = 15 * 60 * 1000

/** What «Programma» proposes when it is chosen: an hour from now, rounded up to the
 *  next quarter hour, as a Rome day and time. Rome's offset is whole hours, so the
 *  quarter hours are the same instants in UTC and in Rome. */
export function defaultSchedule(now = new Date()): { giorno: string; ora: string } {
  const inAnHour = now.getTime() + 60 * 60 * 1000
  return romeToday(new Date(Math.ceil(inAnHour / QUARTER_HOUR_MS) * QUARTER_HOUR_MS))
}

/** The server's column limits (`rebase_core/models.py`, `CAMPAIGN_*_MAX_LENGTH`), so
 *  a field stops where the API would refuse it instead of failing the save. */
export const CAMPAIGN_MAX_LENGTH = { nome: 120, oggetto: 200, testo: 5000, bottone_testo: 60 } as const

const romeDay = new Intl.DateTimeFormat('it-IT', { timeZone: 'Europe/Rome', day: 'numeric', month: 'long', year: 'numeric' })
const romeClock = new Intl.DateTimeFormat('it-IT', { timeZone: 'Europe/Rome', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })

/** The campaign page's line on when it leaves or left, always in Rome time -- the
 *  time the admin scheduled it in, whatever the browser's own zone. `null` for a
 *  draft, or a campaign cancelled before it left. */
export function campaignMoment(campagna: Pick<Campaign, 'stato' | 'programmata_per' | 'inviata_at'>): string | null {
  const { stato, programmata_per, inviata_at } = campagna
  if ((stato === 'programmata' || stato === 'in_invio') && programmata_per) {
    const at = new Date(programmata_per)
    return `Parte il ${romeDay.format(at)} alle ${romeClock.format(at)} (ora di Roma)`
  }
  if (inviata_at) {
    const at = new Date(inviata_at)
    return `Inviata il ${romeDay.format(at)} alle ${romeClock.format(at)}`
  }
  return null
}

type Stoppable = Pick<Campaign, 'stato' | 'fermo_at' | 'fermo_motivo'>

/** A send the tick stopped on something that will not fix itself (REB-524): it stays
 *  `in_invio` and is tried every minute, so its state alone would read as moving. */
export function isStalled(campagna: Stoppable): boolean {
  return (campagna.stato === 'programmata' || campagna.stato === 'in_invio') && Boolean(campagna.fermo_motivo)
}

/** The state as the badge says it: «Invio fermo» rather than «In invio» for a stopped send. */
export function campaignStateLabel(campagna: Stoppable): string {
  return isStalled(campagna) ? 'Invio fermo' : CAMPAIGN_STATE_LABELS[campagna.stato]
}

/** The campaign page's line in place of «Parte il…» while the send is stopped. */
export function stallLine(campagna: Stoppable): string | null {
  if (!isStalled(campagna)) return null
  const since = campagna.fermo_at
    ? `, dal ${romeDay.format(new Date(campagna.fermo_at))} alle ${romeClock.format(new Date(campagna.fermo_at))} (ora di Roma)`
    : ''
  return `Invio fermo: ${campagna.fermo_motivo}${since}. Si riprova ogni minuto e riparte da solo appena è risolto.`
}

/** A moment's time of day in Rome, «10:32»: when the draft was saved, when the test left. */
export function romeTime(iso: string): string {
  return romeClock.format(new Date(iso))
}

/** «1 persona», «83 persone»: the count on the send button. */
export function peopleLabel(count: number): string {
  return `${count} ${count === 1 ? 'persona' : 'persone'}`
}

const scheduleDay = new Intl.DateTimeFormat('it-IT', { timeZone: 'UTC', weekday: 'short', day: 'numeric', month: 'short' })

/** The «Programma» inputs as the send button says them, «lun 28 set, 09:00». The
 *  inputs already hold a Rome day and time, so the day is formatted as a calendar
 *  date (at UTC) and never shifted through the browser's own zone. */
export function scheduleLabel(giorno: string, ora: string): string {
  const [year, month, day] = giorno.split('-').map(Number)
  return `${scheduleDay.format(new Date(Date.UTC(year!, month! - 1, day!)))}, ${ora}`
}

const POLL_MS = 10_000
/** Resend's delivery and bounce events land seconds to minutes after the last mail. */
const AFTER_SEND_MS = 5 * 60 * 1000

/** How often the campaign page rereads itself: while it is scheduled or sending, and
 *  for five minutes after it was sent, while the deliveries come in; then never. */
export function refetchEvery(campagna: Pick<Campaign, 'stato' | 'inviata_at'>, now = Date.now()): number | false {
  if (campagna.stato === 'programmata' || campagna.stato === 'in_invio') return POLL_MS
  if (campagna.stato === 'inviata' && campagna.inviata_at && now - Date.parse(campagna.inviata_at) < AFTER_SEND_MS) {
    return POLL_MS
  }
  return false
}

const LIST_POLL_MS = 30_000

/** How often «Campagne» rereads itself (REB-524): while any campaign is scheduled or
 *  sending, so a send that stops, or ends, shows without a reload; otherwise never.
 *  Slower than the campaign page's `refetchEvery`, since it reads every campaign. */
export function listRefetchEvery(items: readonly Pick<Campaign, 'stato'>[]): number | false {
  return items.some((item) => item.stato === 'programmata' || item.stato === 'in_invio') ? LIST_POLL_MS : false
}

/** The campaign page's figure for the action, once done. */
export const AZIONE_FATTA_LABELS: Record<CampaignAzione, string> = {
  entrato: 'Sono entrati',
  cv: 'Hanno caricato il CV',
  scheda_completa: 'Hanno completato la scheda',
  profilo_creato: 'Hanno creato il profilo',
  richiesta_aggiornata: 'Hanno aggiornato la richiesta',
  pigro_cliente: 'Primo cliente in Pigro',
}

const AZIONE_BREVE: Record<CampaignAzione, string> = {
  entrato: 'entrati',
  cv: 'CV caricati',
  scheda_completa: 'schede completate',
  profilo_creato: 'profili creati',
  richiesta_aggiornata: 'richieste aggiornate',
  pigro_cliente: 'primi clienti',
}

/** A part of the mails sent, as the campaign page's figures say it: «38%», and nothing
 *  while none has left. */
export function share(part: number, whole: number): string | undefined {
  if (whole <= 0) return undefined
  return `${Math.round((part / whole) * 100)}%`
}

/** The list page's «Esito»: what left and what it led to, then what went wrong only when
 *  something did. For `entrato`, entering is the action, so it is said once. */
export function outcomeLine(c: CampaignCounts, azione: CampaignAzione): string {
  const parts = [`${c.inviate} inviate`, `${c.consegnate} consegnate`, `${c.cliccate} clic`, `${c.entrate} entrati`]
  if (azione !== 'entrato') parts.push(`${c.azioni} ${AZIONE_BREVE[azione]}`)
  if (c.rimbalzate) parts.push(`${c.rimbalzate} rimbalzate`)
  if (c.saltate) parts.push(`${c.saltate} saltate`)
  if (c.fallite) parts.push(`${c.fallite} fallite`)
  return parts.join(' · ')
}
