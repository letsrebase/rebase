import { fieldErrorFrom, type ProblemDetail } from '@/lib/api'
import type { components } from '@/lib/api-types'

type PointIn = components['schemas']['ContactPointIn']
type PointRead = components['schemas']['ContactPointRead']

export type PointKind = PointIn['kind']
export type PointLabel = PointIn['label']

/** The request key every entity that owns contact points reads them from. */
export const CONTACT_POINTS_KEY = 'contact_points'

/** The API refuses a longer list (`MAX_CONTACT_POINTS`) and a longer note. */
export const MAX_CONTACT_POINTS = 30
export const NOTE_MAX_LENGTH = 200

/** The kinds in the order a detail page lists them and an add button offers them. */
export const POINT_KINDS: readonly PointKind[] = ['email', 'phone', 'linkedin']
export const POINT_LABELS: readonly PointLabel[] = ['work', 'personal', 'billing', 'other']

export const KIND_TEXT: Record<PointKind, string> = {
  email: 'Email',
  phone: 'Telefono',
  linkedin: 'LinkedIn',
}

export const LABEL_TEXT: Record<PointLabel, string> = {
  work: 'lavoro',
  personal: 'personale',
  billing: 'amministrazione',
  other: 'altro',
}

/**
 * One point as the form holds it. Every input is text until it is sent, so what was typed is
 * never rewritten under the cursor. `id` and `position` of a stored point are not carried
 * on purpose: a write sends the whole list and the server numbers it again.
 */
export interface ContactPointDraft {
  /** Client-only identity of the row, so removing or moving one never shifts another's inputs. */
  key: string
  kind: PointKind
  value: string
  label: PointLabel
  note: string
  is_primary: boolean
}

let nextKey = 0
function newKey(): string {
  nextKey += 1
  return `contact-point-${nextKey}`
}

/** A new row: its kind is the one of the row above it (a second number is the usual
 *  next thing to add), and it is the primary of that kind when it is the first one. */
export function emptyPoint(existing: readonly ContactPointDraft[]): ContactPointDraft {
  const kind = existing.at(-1)?.kind ?? 'email'
  return {
    key: newKey(),
    kind,
    value: '',
    label: 'work',
    note: '',
    is_primary: !existing.some((point) => point.kind === kind),
  }
}

/** The drafts of a stored list, in the position the server holds them. */
export function pointsToDrafts(points: readonly PointRead[] | undefined): ContactPointDraft[] {
  return [...(points ?? [])]
    .sort((a, b) => a.position - b.position)
    .map((point) => ({
      key: newKey(),
      kind: point.kind,
      value: point.value,
      label: point.label,
      note: point.note ?? '',
      is_primary: point.is_primary,
    }))
}

/** A row with no value typed is not a point yet: it is left out of the request. */
export function isSendable(point: ContactPointDraft): boolean {
  return point.value.trim() !== ''
}

/**
 * Gives every kind that has points exactly one primary: the one named by `prefer` when it is
 * flagged, else the first flagged, else the first of the kind. It is what makes «the first
 * point of a kind is primary» and «removing the primary promotes the next» one rule.
 */
function settlePrimaries(
  points: readonly ContactPointDraft[],
  prefer?: string,
): ContactPointDraft[] {
  const chosen = new Map<PointKind, string>()
  for (const kind of POINT_KINDS) {
    const ofKind = points.filter((point) => point.kind === kind)
    if (ofKind.length === 0) continue
    const flagged = ofKind.filter((point) => point.is_primary)
    const winner =
      flagged.find((point) => point.key === prefer) ?? flagged[0] ?? ofKind[0]
    if (winner) chosen.set(kind, winner.key)
  }
  return points.map((point) => {
    const primary = chosen.get(point.kind) === point.key
    return point.is_primary === primary ? point : { ...point, is_primary: primary }
  })
}

export function addPoint(points: readonly ContactPointDraft[]): ContactPointDraft[] {
  return [...points, emptyPoint(points)]
}

export function removePoint(points: readonly ContactPointDraft[], key: string): ContactPointDraft[] {
  return settlePrimaries(points.filter((point) => point.key !== key))
}

export function editPoint(
  points: readonly ContactPointDraft[],
  key: string,
  change: Partial<Pick<ContactPointDraft, 'value' | 'label' | 'note'>>,
): ContactPointDraft[] {
  return points.map((point) => (point.key === key ? { ...point, ...change } : point))
}

/** Changing a row's kind takes it out of the old kind (whose next point is promoted when it
 *  was the primary) and into the new one, where it is primary only if it is the first. */
export function changeKind(
  points: readonly ContactPointDraft[],
  key: string,
  kind: PointKind,
): ContactPointDraft[] {
  return settlePrimaries(
    points.map((point) => (point.key === key ? { ...point, kind, is_primary: false } : point)),
  )
}

/** Ticking a row makes it the primary of its kind and clears the flag of the others of that
 *  kind. A kind with points always has one, so a primary cannot be un-ticked: tick another. */
export function makePrimary(points: readonly ContactPointDraft[], key: string): ContactPointDraft[] {
  const target = points.find((point) => point.key === key)
  if (!target) return [...points]
  return settlePrimaries(
    points.map((point) =>
      point.kind === target.kind ? { ...point, is_primary: point.key === key } : point,
    ),
    key,
  )
}

/** Moves a row one place up (`-1`) or down (`1`); the ends stay where they are. */
export function movePoint(
  points: readonly ContactPointDraft[],
  key: string,
  step: -1 | 1,
): ContactPointDraft[] {
  const from = points.findIndex((point) => point.key === key)
  const to = from + step
  if (from === -1 || to < 0 || to >= points.length) return [...points]
  const next = [...points]
  const [moved] = next.splice(from, 1)
  if (moved) next.splice(to, 0, moved)
  return next
}

/**
 * The request's list, from the rows that have a value: no `id`, no `position`, the value and
 * the note trimmed, an empty note sent as `null`. A kind left with points and no primary
 * (its primary row was blank) gets its first one flagged, as the server would.
 */
export function pointsToBody(drafts: readonly ContactPointDraft[]): PointIn[] {
  const sendable = settlePrimaries(drafts.filter(isSendable))
  return sendable.map((point) => ({
    kind: point.kind,
    value: point.value.trim(),
    label: point.label,
    note: point.note.trim() === '' ? null : point.note.trim(),
    is_primary: point.is_primary,
  }))
}

/**
 * What a form puts in its request under `contact_points`, or `undefined` for nothing to say:
 * a new record sends the list when it has points; an edit sends the whole list only when it
 * differs from the one it opened with (an omitted list leaves the stored one, `[]` clears it).
 */
export function pointsForRequest(
  drafts: readonly ContactPointDraft[],
  initial: readonly ContactPointDraft[] | undefined,
): PointIn[] | undefined {
  const body = pointsToBody(drafts)
  if (initial === undefined) return body.length > 0 ? body : undefined
  return JSON.stringify(body) === JSON.stringify(pointsToBody(initial)) ? undefined : body
}

/** Addresses in the list that the record did not already have: what the Gmail sync notice is for. */
export function newEmailAddresses(
  drafts: readonly ContactPointDraft[],
  initial: readonly ContactPointDraft[] | undefined,
): string[] {
  const known = new Set(
    (initial ?? [])
      .filter((point) => point.kind === 'email' && isSendable(point))
      .map((point) => emailKey(point.value)),
  )
  return drafts
    .filter((point) => point.kind === 'email' && isSendable(point))
    .map((point) => point.value.trim())
    .filter((address) => !known.has(emailKey(address)))
}

// -- Validation, as far as the browser can say it before the request ---------------------

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/
const PHONE_MIN_DIGITS = 3
const PHONE_MAX_DIGITS = 20

function emailKey(value: string): string {
  return value.trim().toLowerCase()
}

function phoneKey(value: string): string {
  const digits = value.replace(/\D/g, '')
  return value.trim().startsWith('+') ? `+${digits}` : digits
}

/** The host and path of a LinkedIn address, or `null` when the value is not one. */
function linkedinKey(value: string): string | null {
  const trimmed = value.trim()
  let url: URL
  try {
    url = new URL(/^[a-z][a-z0-9+-]*:/i.test(trimmed) ? trimmed : `https://${trimmed}`)
  } catch {
    return null
  }
  const host = url.hostname.toLowerCase()
  const onLinkedin = host === 'linkedin.com' || host.endsWith('.linkedin.com')
  if (!onLinkedin || (url.protocol !== 'https:' && url.protocol !== 'http:')) return null
  const profile = /^\/in\/([^/]*)/i.exec(url.pathname)
  const path = profile ? `/in/${decodeURIComponent(profile[1] ?? '').toLowerCase()}` : url.pathname
  return `linkedin.com${path.replace(/\/+$/, '')}`
}

/** The sentence under a row whose value is not what its kind says, or `null` when it is. */
function valueProblem(point: ContactPointDraft): string | null {
  const value = point.value.trim()
  if (point.kind === 'email') {
    return EMAIL_RE.test(value) ? null : 'Indirizzo email non valido (atteso: nome@dominio.it)'
  }
  if (point.kind === 'phone') {
    const digits = value.replace(/\D/g, '').length
    return digits >= PHONE_MIN_DIGITS && digits <= PHONE_MAX_DIGITS
      ? null
      : 'Numero di telefono non valido (da 3 a 20 cifre)'
  }
  return linkedinKey(value) === null
    ? 'Serve l’indirizzo di un profilo su linkedin.com'
    : null
}

function comparisonKey(point: ContactPointDraft): string {
  const value = point.value.trim()
  if (point.kind === 'email') return emailKey(value)
  if (point.kind === 'phone') return phoneKey(value)
  return linkedinKey(value) ?? value.toLowerCase()
}

/**
 * The rows the server would refuse, by row key, with its sentence: a value that is not what
 * its kind says, and a value already above it in the list (same kind, same address, same
 * digits). A blank row is not checked: it is dropped on submit.
 */
export function validatePoints(drafts: readonly ContactPointDraft[]): Record<string, string> {
  const problems: Record<string, string> = {}
  const seen = new Set<string>()
  for (const point of drafts) {
    if (!isSendable(point)) continue
    const invalid = valueProblem(point)
    if (invalid !== null) {
      problems[point.key] = invalid
      continue
    }
    const identity = `${point.kind}|${comparisonKey(point)}`
    if (seen.has(identity)) problems[point.key] = 'Questo recapito è già nell’elenco'
    seen.add(identity)
  }
  return problems
}

/** What a form shows around the editor: a sentence per row, and one for the whole list. */
export interface ContactPointErrors {
  rows: Record<string, string>
  list: string | null
}

/**
 * Where the server's sentence about the list belongs. `ValidationFailed` names the row as
 * `contact_points[i]`, `i` being the position among the rows that were sent; the blank rows
 * were not, so the index is mapped back to the draft. A sentence about the whole list (too
 * many points) has no row.
 */
export function serverPointProblem(
  problem: ProblemDetail | null | undefined,
  drafts: readonly ContactPointDraft[],
): { key: string | null; message: string } | null {
  if (!problem) return null
  const error = fieldErrorFrom(problem)
  if (error === null) return null
  const row = new RegExp(`^${CONTACT_POINTS_KEY}\\[(\\d+)\\]$`).exec(error.field)
  if (row !== null) {
    const sent = drafts.filter(isSendable)
    return { key: sent[Number(row[1])]?.key ?? null, message: error.message }
  }
  return error.field === CONTACT_POINTS_KEY ? { key: null, message: error.message } : null
}

/**
 * The editor's errors: the browser's own checks (once the person pressed Salva, and live
 * after that) and, from a refused save, the server's sentence on the row it names. The
 * browser's check wins on a row both have something to say about.
 */
export function pointErrors(
  drafts: readonly ContactPointDraft[],
  problem: ProblemDetail | null | undefined,
  checked: boolean,
): ContactPointErrors {
  const rows: Record<string, string> = {}
  let list: string | null = null
  const refused = serverPointProblem(problem, drafts)
  if (refused !== null) {
    if (refused.key === null) list = refused.message
    else rows[refused.key] = refused.message
  }
  if (checked) {
    Object.assign(rows, validatePoints(drafts))
    if (drafts.filter(isSendable).length > MAX_CONTACT_POINTS) {
      list = `Al massimo ${MAX_CONTACT_POINTS} recapiti`
    }
  }
  return { rows, list }
}

/** True when the browser's own checks pass: the form may send. */
export function pointsAreSendable(drafts: readonly ContactPointDraft[]): boolean {
  return (
    Object.keys(validatePoints(drafts)).length === 0 &&
    drafts.filter(isSendable).length <= MAX_CONTACT_POINTS
  )
}

/** True for a problem the editor draws itself, so the form's generic banner leaves it out. */
export function isPointProblem(problem: ProblemDetail | null | undefined): boolean {
  const field = problem?.field
  return (
    problem?.code === 'validation_failed' &&
    typeof field === 'string' &&
    (field === CONTACT_POINTS_KEY || field.startsWith(`${CONTACT_POINTS_KEY}[`))
  )
}

// -- Reading: the link a detail page draws -----------------------------------------------

/**
 * Where a point links to: `mailto:` for an address, `tel:` for a number (the digits and a
 * leading plus only: spaces, dashes and brackets break some dialers), the profile's own
 * address for LinkedIn. A profile whose value is somehow not an http(s) address is not linked.
 */
export function pointHref(point: { kind: PointKind; value: string }): string | null {
  const value = point.value.trim()
  if (point.kind === 'email') return `mailto:${value}`
  if (point.kind === 'phone') {
    return `tel:${value.startsWith('+') ? '+' : ''}${value.replace(/\D/g, '')}`
  }
  try {
    const url = new URL(value)
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null
  } catch {
    return null
  }
}
