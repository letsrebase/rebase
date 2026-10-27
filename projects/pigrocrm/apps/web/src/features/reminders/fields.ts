import type { StatusTone } from '@/components/StatusPill'
import type { components } from '@/lib/api-types'

export type Reminder = components['schemas']['ReminderRead']
export type Severity = Reminder['severity']
export type SnoozeOption = components['schemas']['SnoozeRequest']['until']

/**
 * The three severities, severest first, with the words the page uses for them.
 *
 * `label` heads a section («Urgenti», «Gravi», «Da guardare»); `single` is the same word
 * for one reminder, on its pill, because the handled list below mixes severities and a row
 * has to say which one it was on its own. The tone is one of the five a state may read as
 * (`StatusPill`): danger for what is urgent, gold («waiting on somebody») for the serious
 * ones, muted for what only asks to be looked at. The colour is never the only carrier:
 * the section and the pill both say the word.
 */
export const SEVERITIES: readonly {
  id: Severity
  label: string
  single: string
  tone: StatusTone
}[] = [
  { id: 'critical', label: 'Urgenti', single: 'Urgente', tone: 'danger' },
  { id: 'serious', label: 'Gravi', single: 'Grave', tone: 'gold' },
  { id: 'warning', label: 'Da guardare', single: 'Da guardare', tone: 'muted' },
]

/**
 * What the API offers to postpone to, in the order it lists them, and never a free date
 * (spec 2026-10-26 §8.2). «Lunedì» is the next Monday, a day the server counts.
 */
export const SNOOZE_OPTIONS: readonly { id: SnoozeOption; label: string }[] = [
  { id: 'tomorrow', label: 'Domani' },
  { id: 'next_monday', label: 'Lunedì' },
  { id: 'in_a_week', label: 'Tra una settimana' },
  { id: 'in_a_month', label: 'Tra un mese' },
]

/** Open means «not yet dealt with by this person»: the page's list and the sidebar's count. */
export function isOpen(reminder: Reminder): boolean {
  return reminder.status === 'open'
}

/**
 * The title without the kind it starts with. The API writes every title as «<label>:
 * <subject>» (`Attività scaduta: Mandare il preventivo ad Acme`) and sends the label on
 * its own as well, and the row draws the label above the title, so repeating it in the
 * title would say it twice. A title that does not start that way is shown whole. The
 * first letter is capitalised either way.
 */
export function subjectOf(reminder: Pick<Reminder, 'label' | 'title'>): string {
  const prefix = `${reminder.label}: `
  const subject =
    reminder.title.startsWith(prefix) && reminder.title.length > prefix.length
      ? reminder.title.slice(prefix.length)
      : reminder.title
  // A sentence starts with a capital, and the API writes the subject of an invoice
  // reminder as «fattura 2026/1 · Acme» because it also builds it into longer texts
  // where it is mid-sentence. The row is a line of its own.
  return subject.charAt(0).toLocaleUpperCase('it-IT') + subject.slice(1)
}

/**
 * What a person did about a reminder that left the main list, in words. An ISO date is
 * read field by field, never through `new Date('2026-10-27')`, which is UTC midnight and
 * renders as the day before anywhere west of Greenwich.
 */
export function handledLine(reminder: Reminder): string {
  if (reminder.status === 'snoozed' && reminder.snoozed_until) {
    const [year, month, day] = reminder.snoozed_until.split('-')
    return `Rimandato fino al ${day}/${month}/${year}`
  }
  return 'Visto'
}
