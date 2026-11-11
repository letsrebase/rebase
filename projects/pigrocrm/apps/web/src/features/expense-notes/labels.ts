import type { StatusTone } from '@/components/StatusPill'
import { formatIsoDateItalian, toIsoDate } from '@/lib/dates'
import { formatItalianQuantity } from '@/lib/decimal'
import { monthOnly } from '@/features/staff/labels'
import type { Bucket, ExpenseNote, NoteKind, NoteStatus } from './queries'

/**
 * Words of «Note spese» (the approved brief of 2026-11-10, REB-1046), inline in Italian as
 * `features/staff/labels.ts` keeps them: the CRM's web app has no locale files. Nothing here
 * computes a figure: every amount, date and name is what the server sent.
 */

export const PAGE_TITLE = 'Note spese'

export const STATUS_LABELS: Record<NoteStatus, string> = {
  submitted: 'Inviata',
  approved: 'Approvata',
  rejected: 'Rifiutata',
  reimbursed: 'Rimborsata',
}

/** Gold waits for somebody, danger is refused, ink is settled (the brief's «Palette»). */
export const STATUS_TONES: Record<NoteStatus, StatusTone> = {
  submitted: 'gold',
  approved: 'gold',
  rejected: 'danger',
  reimbursed: 'ink',
}

export const TABS: readonly { id: Bucket; label: string }[] = [
  { id: 'to_approve', label: 'Da approvare' },
  { id: 'to_reimburse', label: 'Da rimborsare' },
  { id: 'closed', label: 'Chiuse' },
]

export const KIND_LABELS: Record<NoteKind, string> = {
  expense: 'Una spesa',
  mileage: 'Chilometri',
}

/** What the first choice of a new note says under each kind. */
export const KIND_HINTS: Record<NoteKind, string> = {
  expense: 'Uno scontrino o una ricevuta, con la sua foto',
  mileage: 'Il viaggio in auto: la tariffa la scrivi tu',
}

export const CHOOSE_KIND = 'Cosa vuoi farti rimborsare?'
export const MILEAGE_RATE_LINE = 'La tariffa la scrivi tu: la tabella ACI non è ancora verificata'

export const EMPTY_STAFF = 'Non hai ancora inviato note spese.'
export const EMPTY_APPROVER: Record<Bucket, string> = {
  to_approve: 'Nessuna nota da approvare.',
  to_reimburse: 'Nessuna nota da rimborsare.',
  closed: 'Nessuna nota chiusa.',
}
export const NO_MATCH = 'Nessuna nota con questi filtri.'
export const NOT_LINKED = "Non risulti tra le persone dell'azienda: chiedi a un amministratore di collegarti."
export const OWN_NOTE = 'Non puoi approvare una tua nota: la approva un altro amministratore.'
export const SOLE_ADMINISTRATOR =
  "Sei l'unico amministratore: la approvi tu e resta segnata come autoapprovata nell'esportazione."
export const AWAITING_ADMINISTRATOR = 'Aspetta un amministratore'
export const TO_REIMBURSE = 'Da rimborsare'

export const formatDay = (value: string): string => formatIsoDateItalian(value)

/** A day from a timestamp, in the reader's own calendar: «02/11/2026». */
export const formatInstantDay = (value: string): string => formatIsoDateItalian(toIsoDate(new Date(value)))

/** «1 nota», «3 note». */
export const notes = (count: number): string => (count === 1 ? '1 nota' : `${count} note`)

/** How a note will be, or was, paid back: «Bonifico del 10/11/2026» or «In busta paga di
 *  novembre»; `null` while nobody has chosen. */
export function wayLine(note: ExpenseNote): string | null {
  if (note.reimbursement_method === 'transfer') {
    return note.reimbursement_date === null ? 'Bonifico' : `Bonifico del ${formatDay(note.reimbursement_date)}`
  }
  if (note.reimbursement_method === 'payslip') {
    return note.payroll_month === null ? 'In busta paga' : `In busta paga di ${monthOnly(note.payroll_month.slice(0, 7))}`
  }
  return null
}

/** The line under a note's pill: what it waits for, how it will be paid, why it was refused.
 *  `forStaffMember` is the reader being the person it is for, who is told what to wait for. */
export function stateLine(note: ExpenseNote, forStaffMember: boolean): string | null {
  switch (note.status) {
    case 'submitted':
      return forStaffMember ? AWAITING_ADMINISTRATOR : null
    case 'approved':
      return wayLine(note) ?? TO_REIMBURSE
    case 'rejected':
      return note.rejection_reason
    case 'reimbursed': {
      const way = wayLine(note) ?? STATUS_LABELS.reimbursed
      // A transfer's own date is the day it left; a payslip's note says when it was settled.
      return note.reimbursement_method === 'payslip' && note.reimbursed_on !== null
        ? `${way}, il ${formatDay(note.reimbursed_on)}`
        : way
    }
  }
}

/** «Da Milano a Torino»: a mileage note's route, whichever end the person wrote. */
export function routeLine(note: ExpenseNote): string | null {
  if (note.route_from === null && note.route_to === null) return null
  return `Da ${note.route_from ?? '?'} a ${note.route_to ?? '?'}`
}

/** «120 km a 0,45 €/km»: the kilometres and the rate the person typed, as the server holds them. */
export function mileageLine(note: ExpenseNote): string | null {
  if (note.kilometres === null || note.rate_per_km === null) return null
  return `${formatItalianQuantity(note.kilometres)} km a ${formatItalianQuantity(note.rate_per_km)} €/km`
}

const AMOUNT = /^\d+(\.\d{1,2})?$/
const KILOMETRES = /^\d+(\.\d{1,3})?$/
const RATE = /^\d+(\.\d{1,4})?$/

function decimal(typed: string, shape: RegExp): string | null {
  const value = typed.trim().replace(',', '.')
  return shape.test(value) ? value : null
}

/** What a person typed as the decimal string the API takes, or `null` when it is not one of that
 *  shape. The comma is accepted because that is how it is written here; nothing is parsed to a
 *  number, and what a figure means (zero, too large) is the server's to refuse. */
export const amountInput = (typed: string): string | null => decimal(typed, AMOUNT)
export const kilometresInput = (typed: string): string | null => decimal(typed, KILOMETRES)
export const rateInput = (typed: string): string | null => decimal(typed, RATE)

/**
 * What an administrator may do with a note now: the acts the server would take, never a disabled
 * button. A submitted note is decided where the server says (`can_decide`, which is only ever true
 * for a submitted note). An approved one is reimbursed by any administrator but its own person,
 * and by the space's only administrator even then (`sole`, the summary's `only_administrator`):
 * the server refuses the same way for the three acts.
 */
export function actionsFor(
  note: ExpenseNote,
  admin: boolean,
  sole: boolean,
): ('approve' | 'reject' | 'reimburse')[] {
  if (!admin) return []
  if (note.status === 'submitted') return note.can_decide ? ['reject', 'approve'] : []
  if (note.status === 'approved') return !note.own || sole ? ['reimburse'] : []
  return []
}

/** Whether the note is the administrator's own and an act on it is refused to them: the sentence of
 *  design § 3 is shown beside it instead of the act. */
export const refusedAsOwn = (note: ExpenseNote, admin: boolean, sole: boolean): boolean =>
  admin && note.own && !sole && (note.status === 'submitted' || note.status === 'approved')
