import type { Campaign, CampaignAction, CampaignCounts, CampaignMeta, CampaignStatus, RecipientStatus } from './api'

export const CAMPAIGN_STATE_LABELS: Record<CampaignStatus, string> = {
  draft: 'Bozza',
  scheduled: 'Programmata',
  sending: 'In invio',
  sent: 'Inviata',
  cancelled: 'Annullata',
}

export const ACTION_LABELS: Record<CampaignAction, string> = {
  signed_in: 'È entrato nell’area',
  cv: 'Ha caricato il CV',
  card_complete: 'Ha completato la scheda',
  profile_created: 'Ha creato il profilo',
  request_updated: 'Ha aggiornato la richiesta',
  pigro_customer: 'Primo cliente in Pigro',
  click: 'Ha cliccato il link',
}

/** Phase 1 offers three: the Pigro button arrives with phase 3. «Un link» leads out of
 *  the hub, to an address the admin writes (REB-530). */
export const META_LABELS: Partial<Record<CampaignMeta, string>> = {
  area: 'La sua area',
  wizard: 'Il wizard del profilo',
  request: 'La richiesta dell’azienda',
  link: 'Un link',
}

export const RECIPIENT_STATE_LABELS: Record<RecipientStatus, string> = {
  queued: 'In coda',
  sent: 'Inviata',
  skipped: 'Saltata',
  failed: 'Fallita',
}

/** The server's rule (`campaigns/render.py:personalise`), for the preview on screen. */
export function personalise(text: string, name: string | null): string {
  if (name) return text.replaceAll('{nome}', name)
  return text.replaceAll(' {nome}', '').replaceAll('{nome}', '')
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
export function subjectFor(subject: string, name: string | null): string {
  return oneLine(personalise(subject, oneLine(name ?? '') || null))
}

/** Today and the current time in Europe/Rome, as the «Programma» inputs want them. */
export function romeToday(now = new Date()): { day: string; time: string } {
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
  return { day: `${parts.year}-${parts.month}-${parts.day}`, time: `${parts.hour}:${parts.minute}` }
}

const QUARTER_HOUR_MS = 15 * 60 * 1000

/** What «Programma» proposes when it is chosen: an hour from now, rounded up to the
 *  next quarter hour, as a Rome day and time. Rome's offset is whole hours, so the
 *  quarter hours are the same instants in UTC and in Rome. */
export function defaultSchedule(now = new Date()): { day: string; time: string } {
  const inAnHour = now.getTime() + 60 * 60 * 1000
  return romeToday(new Date(Math.ceil(inAnHour / QUARTER_HOUR_MS) * QUARTER_HOUR_MS))
}

/** The server's column limits (`rebase_core/models.py`, `CAMPAIGN_*_MAX_LENGTH`), so
 *  a field stops where the API would refuse it instead of failing the save. */
export const CAMPAIGN_MAX_LENGTH = { name: 120, subject: 200, text: 5000, button_text: 60, button_url: 500 } as const

const romeDay = new Intl.DateTimeFormat('it-IT', { timeZone: 'Europe/Rome', day: 'numeric', month: 'long', year: 'numeric' })
const romeClock = new Intl.DateTimeFormat('it-IT', { timeZone: 'Europe/Rome', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })

/** The campaign page's line on when it leaves or left, always in Rome time -- the
 *  time the admin scheduled it in, whatever the browser's own zone. `null` for a
 *  draft, or a campaign cancelled before it left. */
export function campaignMoment(campaign: Pick<Campaign, 'status' | 'scheduled_for' | 'sent_at'>): string | null {
  const { status, scheduled_for, sent_at } = campaign
  if ((status === 'scheduled' || status === 'sending') && scheduled_for) {
    const at = new Date(scheduled_for)
    return `Parte il ${romeDay.format(at)} alle ${romeClock.format(at)} (ora di Roma)`
  }
  if (sent_at) {
    const at = new Date(sent_at)
    return `Inviata il ${romeDay.format(at)} alle ${romeClock.format(at)}`
  }
  return null
}

type Stoppable = Pick<Campaign, 'status' | 'stalled_at' | 'stalled_reason'>

/** A send the tick stopped on something that will not fix itself (REB-524): it stays
 *  `sending` and is tried every minute, so its state alone would read as moving. */
export function isStalled(campaign: Stoppable): boolean {
  return (campaign.status === 'scheduled' || campaign.status === 'sending') && Boolean(campaign.stalled_reason)
}

/** The state as the badge says it: «Invio fermo» rather than «In invio» for a stopped send. */
export function campaignStateLabel(campaign: Stoppable): string {
  return isStalled(campaign) ? 'Invio fermo' : CAMPAIGN_STATE_LABELS[campaign.status]
}

/** The campaign page's line in place of «Parte il…» while the send is stopped. */
export function stallLine(campaign: Stoppable): string | null {
  if (!isStalled(campaign)) return null
  const since = campaign.stalled_at
    ? `, dal ${romeDay.format(new Date(campaign.stalled_at))} alle ${romeClock.format(new Date(campaign.stalled_at))} (ora di Roma)`
    : ''
  return `Invio fermo: ${campaign.stalled_reason}${since}. Si riprova ogni minuto e riparte da solo appena è risolto.`
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
export function scheduleLabel(date: string, time: string): string {
  const [year, month, day] = date.split('-').map(Number)
  return `${scheduleDay.format(new Date(Date.UTC(year!, month! - 1, day!)))}, ${time}`
}

const POLL_MS = 10_000
/** Resend's delivery and bounce events land seconds to minutes after the last mail. */
const AFTER_SEND_MS = 5 * 60 * 1000

/** How often the campaign page rereads itself: while it is scheduled or sending, and
 *  for five minutes after it was sent, while the deliveries come in; then never. */
export function refetchEvery(campaign: Pick<Campaign, 'status' | 'sent_at'>, now = Date.now()): number | false {
  if (campaign.status === 'scheduled' || campaign.status === 'sending') return POLL_MS
  if (campaign.status === 'sent' && campaign.sent_at && now - Date.parse(campaign.sent_at) < AFTER_SEND_MS) {
    return POLL_MS
  }
  return false
}

const LIST_POLL_MS = 30_000

/** How often «Campagne» rereads itself (REB-524): while any campaign is scheduled or
 *  sending, so a send that stops, or ends, shows without a reload; otherwise never.
 *  Slower than the campaign page's `refetchEvery`, since it reads every campaign. */
export function listRefetchEvery(items: readonly Pick<Campaign, 'status'>[]): number | false {
  return items.some((item) => item.status === 'scheduled' || item.status === 'sending') ? LIST_POLL_MS : false
}

/** The campaign page's figure for the action, once done. */
export const ACTION_DONE_LABELS: Record<CampaignAction, string> = {
  signed_in: 'Sono entrati',
  cv: 'Hanno caricato il CV',
  card_complete: 'Hanno completato la scheda',
  profile_created: 'Hanno creato il profilo',
  request_updated: 'Hanno aggiornato la richiesta',
  pigro_customer: 'Primo cliente in Pigro',
  click: 'Hanno cliccato il link',
}

const ACTION_SHORT: Record<CampaignAction, string> = {
  signed_in: 'entrati',
  cv: 'CV caricati',
  card_complete: 'schede completate',
  profile_created: 'profili creati',
  request_updated: 'richieste aggiornate',
  pigro_customer: 'primi clienti',
  click: 'clic sul link',
}

/** A part of the mails sent, as the campaign page's figures say it: «38%», and nothing
 *  while none has left. */
export function share(part: number, whole: number): string | undefined {
  if (whole <= 0) return undefined
  return `${Math.round((part / whole) * 100)}%`
}

/** The list page's «Esito»: what left and what it led to, then what went wrong only when
 *  something did. For `signed_in`, entering is the action, and for `click` the click is
 *  (REB-530), so each is said once. */
export function outcomeLine(c: CampaignCounts, action: CampaignAction): string {
  const parts = [`${c.sent} inviate`, `${c.delivered} consegnate`, `${c.clicked} clic`, `${c.income} entrati`]
  if (action !== 'signed_in' && action !== 'click') parts.push(`${c.actions} ${ACTION_SHORT[action]}`)
  if (c.bounced) parts.push(`${c.bounced} rimbalzate`)
  if (c.skipped) parts.push(`${c.skipped} saltate`)
  if (c.failed) parts.push(`${c.failed} fallite`)
  return parts.join(' · ')
}
