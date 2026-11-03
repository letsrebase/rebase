import type { components } from '@/lib/api-types'
import type { WorkUnitRow } from './labels'

export type ApprovalRecordBody = components['schemas']['ApprovalRecord']
export type ApprovalRecorded = components['schemas']['ApprovalRecorded']
export type ApprovalChannel = ApprovalRecordBody['channel']
export type GmailMessage = components['schemas']['GmailMessageRead']

/** The channels a notice can arrive by, in the words a person uses (mastro's `noticeChannel`). */
export const Channels: Record<ApprovalChannel, string> = {
  email: 'Email',
  certified_mail: 'Posta certificata',
  registered_mail: 'Raccomandata',
  courier: 'Corriere',
  other: 'Altro',
}

/** The channel's words, or the stored code for one this build does not know. */
export function channelLabel(channel: string): string {
  return Object.hasOwn(Channels, channel) ? Channels[channel as ApprovalChannel] : channel
}

export const ChannelIds = Object.keys(Channels) as ApprovalChannel[]

/** The two states a recorded approval can still use: the API refuses a day in any other. */
const APPROVABLE = new Set(['proposed', 'worked_without_approval'])

export function canReceiveApproval(day: Pick<WorkUnitRow, 'status' | 'approval_id'>): boolean {
  return APPROVABLE.has(day.status) && day.approval_id === null
}

/** Collapses every run of whitespace, so a quotation wrapped differently in the mail
 *  than where the person pasted it still matches. */
function squash(text: string): string {
  return text.replace(/\s+/g, ' ').trim().toLowerCase()
}

/**
 * Whether the excerpt the person typed is a passage of the message they chose as proof.
 * A hint and never a gate: an approval is evidence and the excerpt is meant to be
 * verbatim, but a mail's text and its HTML do not always agree, so a mismatch is said and
 * the person decides.
 */
export function excerptIsInBody(excerpt: string, body: string): boolean {
  const wanted = squash(excerpt)
  return wanted === '' || squash(body).includes(wanted)
}

/** `datetime-local` wants `YYYY-MM-DDTHH:mm` in local time, which `toISOString` is not. */
export function toDateTimeLocal(value: Date): string {
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}T${pad(value.getHours())}:${pad(value.getMinutes())}`
}

export interface NewApprovalSearch {
  contract?: string
  /** The days the approval is opened for, comma separated ids. */
  days?: string
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export function validateNewApprovalSearch(search: Record<string, unknown>): NewApprovalSearch {
  const contract = typeof search.contract === 'string' && UUID.test(search.contract) ? search.contract : undefined
  const ids =
    typeof search.days === 'string'
      ? search.days
          .split(',')
          .filter((id) => UUID.test(id))
          .join(',')
      : ''
  return { contract, days: ids === '' ? undefined : ids }
}
