import type { StatusTone } from '@/components/StatusPill'
import type { components } from '@/lib/api-types'

export type StoredExport = components['schemas']['ExportRead']
export type ExportPart = components['schemas']['PartPreviewRead']

/** What `POST /exports` takes. */
export interface BuildRequest {
  date_from: string
  date_to: string
  legal_entity_id: string | null
  all_entities: boolean
}

/** What the press of «Crea esportazione» asks for, with the words its row in the list wears
 *  while it is being made. */
export interface BuildTarget {
  request: BuildRequest
  company: string
  period: string
}

/** A build the page asked for and the server has not answered, or answered with a refusal. It is
 *  not a stored row: a failed one is only here, until it is retried or closed. */
export interface Attempt {
  key: string
  target: BuildTarget
  status: 'building' | 'failed'
  reason: string | null
}

/** How many exports one page of the list holds. */
export const PAGE_SIZE = 50

/** The state of an export as a person reads it. A build that failed is not a stored row: it is
 *  the page's own attempt, kept until it is retried or closed. */
export type ExportStatus = 'building' | 'ready' | 'failed' | 'deleted'

export const STATUS_LABELS: Record<ExportStatus, string> = {
  building: 'In preparazione',
  ready: 'Pronta',
  failed: 'Non riuscita',
  deleted: 'Eliminata',
}

export const STATUS_TONES: Record<ExportStatus, StatusTone> = {
  building: 'gold',
  ready: 'ink',
  failed: 'danger',
  deleted: 'muted',
}

/** What the check says about the export as it was made: open points never stop it, so the
 *  worse reading is the waiting gold and not the danger one. */
export type ControlState = 'complete' | 'open'

export const CONTROL_TONES: Record<ControlState, StatusTone> = {
  complete: 'ink',
  open: 'gold',
}

export function controlLabel(state: ControlState, open: number): string {
  if (state === 'complete') return 'Completa'
  return open === 1 ? '1 aperto' : `${open} aperti`
}

export function statusOf(item: StoredExport): ExportStatus {
  return item.deleted_at === null ? 'ready' : 'deleted'
}

/** One line of the index a build wrote: a file with its row count, a folder with its file
 *  count, or a part left out. */
export interface IndexLine {
  section: string
  key: string
  label: string
  status: 'included' | 'omitted' | 'unavailable'
  /** Why a part was left out: the fiscal pack of the company (named on the page) or a module
   *  that is off (never named). A snapshot written before the cause was kept has none. */
  cause: 'module' | 'pack' | null
  path: string | null
  count: number | null
  unit: 'rows' | 'files' | null
}

/** A document the build expected to attach and did not find. */
export interface MissingDocument {
  section: string
  document: string
  kind: string
  reason: string
}

export interface ExportIndex {
  lines: IndexLine[]
  missing: MissingDocument[]
}

const text = (value: unknown): string => (typeof value === 'string' ? value : '')

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

/**
 * The snapshot an export keeps (`checklist`, a free-form object in the API's schema) read into
 * what the panel draws. Anything that is not the shape the build writes is left out rather than
 * guessed, so an odd row never breaks the panel.
 */
export function readIndex(checklist: Record<string, unknown>): ExportIndex {
  const lines: IndexLine[] = []
  for (const raw of Array.isArray(checklist.parts) ? checklist.parts : []) {
    const line = record(raw)
    if (line === null) continue
    const status = line.status
    if (status !== 'included' && status !== 'omitted' && status !== 'unavailable') continue
    lines.push({
      section: text(line.section),
      key: text(line.key),
      label: text(line.label),
      status,
      cause: line.cause === 'module' || line.cause === 'pack' ? line.cause : null,
      path: typeof line.path === 'string' ? line.path : null,
      count: typeof line.count === 'number' ? line.count : null,
      unit: line.unit === 'rows' || line.unit === 'files' ? line.unit : null,
    })
  }
  const missing: MissingDocument[] = []
  for (const raw of Array.isArray(checklist.missing) ? checklist.missing : []) {
    const item = record(raw)
    if (item === null) continue
    missing.push({
      section: text(item.section),
      document: text(item.document),
      kind: text(item.kind),
      reason: text(item.reason),
    })
  }
  return { lines, missing }
}

/** The parts an export holds, counted once each (a part may write a list and a folder). */
export function partCount(index: ExportIndex): number {
  return new Set(
    index.lines.filter((line) => line.status === 'included').map((line) => `${line.section}/${line.key}`),
  ).size
}

export function partsWord(count: number): string {
  return count === 1 ? '1 parte' : `${count} parti`
}

/** «12 righe» / «1 riga», «3 file» / «1 file». */
export function quantity(count: number, unit: 'rows' | 'files'): string {
  if (unit === 'files') return `${count} file`
  return count === 1 ? '1 riga' : `${count} righe`
}

const KIB = 1024
const MIB = KIB * KIB
const size = new Intl.NumberFormat('it-IT', { maximumFractionDigits: 1, minimumFractionDigits: 1 })

/** A ZIP's size as the brief writes it: «38,4 MiB», «512,0 KiB», never a unit above the mebibyte. */
export function formatMebibytes(bytes: number): string {
  if (bytes >= MIB) return `${size.format(bytes / MIB)} MiB`
  if (bytes >= KIB) return `${size.format(bytes / KIB)} KiB`
  return `${bytes} B`
}

const dayTime = new Intl.DateTimeFormat('it-IT', {
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
})

/** «10/11/2026 10:40», in the reader's own zone. */
export function formatDateTime(isoMoment: string): string {
  return dayTime.format(new Date(isoMoment)).replace(',', '')
}

const day = new Intl.DateTimeFormat('it-IT', { day: '2-digit', month: '2-digit', year: 'numeric' })

/** «10/11/2026». */
export function formatDay(isoMoment: string): string {
  return day.format(new Date(isoMoment))
}

/** The numbers of the first few missing documents and «e altre N»: the full list is in the
 *  index inside the ZIP. */
export const MISSING_SHOWN = 5

export function andOthers(total: number): string | null {
  return total > MISSING_SHOWN ? `e altre ${total - MISSING_SHOWN}` : null
}
