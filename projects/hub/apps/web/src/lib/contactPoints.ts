import type { ContactPoint, ContactPointWrite } from './api'

/** REB-944: a person's extra ways to be reached. The list a page reads starts with the
 *  address they sign in with, as the primary email and without an `id`: it is the identity,
 *  never edited from the list and never sent back. Everything after it is a point the
 *  person or an admin added, and the list is written whole. */

export const CONTACT_KINDS = ['email', 'phone', 'linkedin'] as const
export type ContactKind = (typeof CONTACT_KINDS)[number]
export const CONTACT_LABELS = ['work', 'personal', 'billing', 'other'] as const
export type ContactLabel = (typeof CONTACT_LABELS)[number]

export const CONTACT_KIND_NAMES: Record<ContactKind, string> = {
  email: 'Email',
  phone: 'Telefono',
  linkedin: 'LinkedIn',
}
export const CONTACT_LABEL_NAMES: Record<ContactLabel, string> = {
  work: 'lavoro',
  personal: 'personale',
  billing: 'amministrazione',
  other: 'altro',
}
export const CONTACT_NOTE_MAX = 200
const PHONE_MIN_DIGITS = 6

/** A point while it is edited: every value is a string, and `key` is what React keeps a row
 *  by (a new row has no `id` yet, and the list is replaced on save). */
export interface PointDraft {
  key: string
  kind: ContactKind
  value: string
  label: ContactLabel
  note: string
  isPrimary: boolean
}

let counter = 0
function nextKey(): string {
  counter += 1
  return `point-${counter}`
}

export function blankDraft(kind: ContactKind = 'phone'): PointDraft {
  return { key: nextKey(), kind, value: '', label: 'work', note: '', isPrimary: false }
}

/** The address the person signs in with: the entry the API sends without an `id`. */
export function loginPoint(points: readonly ContactPoint[]): ContactPoint | null {
  return points.find((point) => point.id === null) ?? null
}

/** The points a person or an admin added: everything but the login address. */
export function extraPoints(points: readonly ContactPoint[]): ContactPoint[] {
  return points.filter((point) => point.id !== null)
}

/** The points that can be edited: everything but the login address. */
export function draftsFrom(points: readonly ContactPoint[]): PointDraft[] {
  return points
    .filter((point) => point.id !== null)
    .map((point) => ({
      key: nextKey(),
      kind: point.kind,
      value: point.value,
      label: point.label,
      note: point.note ?? '',
      isPrimary: point.is_primary,
    }))
}

/** What the API takes: the drafts with a value, in order. A row left blank is dropped, not
 *  refused, so «Aggiungi» followed by a change of mind costs nothing. */
export function toWrite(drafts: readonly PointDraft[]): ContactPointWrite[] {
  return drafts
    .filter((draft) => draft.value.trim() !== '')
    .map((draft) => ({
      kind: draft.kind,
      value: draft.value.trim(),
      label: draft.label,
      note: draft.note.trim() === '' ? null : draft.note.trim(),
      is_primary: draft.isPrimary,
    }))
}

/** What is wrong with one row, in the words the form shows, or `null`. The server decides:
 *  this only keeps an obvious slip from costing a round trip. */
export function problemOf(draft: PointDraft): string | null {
  const value = draft.value.trim()
  if (value === '') return null
  if (draft.kind === 'email' && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) {
    return 'Serve un indirizzo email valido.'
  }
  if (draft.kind === 'phone' && value.replace(/\D/g, '').length < PHONE_MIN_DIGITS) {
    return `Serve un numero di telefono, di almeno ${PHONE_MIN_DIGITS} cifre.`
  }
  if (draft.kind === 'linkedin' && !/linkedin\.com/i.test(value)) {
    return 'Serve l’indirizzo di un profilo LinkedIn.'
  }
  if (draft.note.trim().length > CONTACT_NOTE_MAX) {
    return `La nota può avere al massimo ${CONTACT_NOTE_MAX} caratteri.`
  }
  return null
}

export function allValid(drafts: readonly PointDraft[]): boolean {
  return drafts.every((draft) => problemOf(draft) === null)
}

/** What follows a point's value on a page: its label and, after a colon, its note, in
 *  brackets. The sign-in address has neither, so nothing follows it. */
export function pointSuffix(point: ContactPoint): string {
  if (point.id === null) return ''
  const label = CONTACT_LABEL_NAMES[point.label]
  return point.note ? ` (${label}: ${point.note})` : ` (${label})`
}
