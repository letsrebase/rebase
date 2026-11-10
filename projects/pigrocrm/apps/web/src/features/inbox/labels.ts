import type { StatusTone } from '@/components/StatusPill'
import type { components } from '@/lib/api-types'

/**
 * «In arrivo» in words (REB-984, the brief of REB-973): the item's state, kind and file
 * type as a person reads them, and the one line that says where each item stands. Every
 * `Record` below is over the API's own union, so a state, a kind or a file type added there
 * is a compile error here and not a raw English word on the screen.
 */

export type InboxItem = components['schemas']['InboxItemRead']
export type InboxItemDetail = components['schemas']['InboxItemDetail']
export type InboxState = InboxItem['state']
export type InboxKind = NonNullable<InboxItem['kind']>
export type InboxFileType = NonNullable<InboxItem['file_type']>

export const STATE_LABELS: Record<InboxState, string> = {
  new: 'Nuovo',
  proposed: 'Da confermare',
  filed: 'Archiviato',
  duplicate: 'Doppione',
  discarded: 'Scartato',
}

/** The tone of each state's pill (the brief): a refused file is `danger` and is drawn only
 *  while it is a row of an upload, never as an item. */
export const STATE_TONES: Record<InboxState, StatusTone> = {
  new: 'muted',
  proposed: 'gold',
  filed: 'ink',
  duplicate: 'muted',
  discarded: 'muted',
}

export const KIND_LABELS: Record<InboxKind, string> = {
  sales_invoice: 'Fattura emessa',
  supplier_invoice: 'Fattura di acquisto',
  credit_note: 'Nota di credito',
  receipt: 'Ricevuta',
  bank_statement: 'Estratto conto',
  contract: 'Contratto',
  tax_document: 'Documento fiscale',
  payslip: 'Cedolino',
  archive: 'Archivio',
  other: 'Altro',
}

/** What a person may say an item is: every kind but the archive, which only a ZIP that was
 *  opened has. The order is the one the design lists them in (§ 1). */
export const PICKABLE_KINDS: readonly InboxKind[] = [
  'sales_invoice',
  'supplier_invoice',
  'credit_note',
  'receipt',
  'bank_statement',
  'contract',
  'tax_document',
  'payslip',
  'other',
]

/** The file type with a plain word beside the extension: nobody here is asked what «p7m» is. */
export const FILE_TYPE_LABELS: Record<InboxFileType, string> = {
  xml: 'Fattura elettronica (XML)',
  p7m: 'Fattura elettronica firmata (.p7m)',
  zip: 'Archivio ZIP',
  pdf: 'PDF',
  png: 'Foto (PNG)',
  jpeg: 'Foto (JPEG)',
  csv: 'Foglio CSV',
  xls: 'Foglio Excel',
  xlsx: 'Foglio Excel',
  camt053: 'Estratto conto (CAMT.053)',
}

/** The three tabs of the page and the states each one lists (the brief): what waits, what
 *  was filed, and what was put aside or seen twice. */
export const TABS = [
  { id: 'pending', label: 'Da gestire', states: ['new', 'proposed'] },
  { id: 'filed', label: 'Archiviati', states: ['filed'] },
  { id: 'set_aside', label: 'Scartati e doppioni', states: ['discarded', 'duplicate'] },
] as const satisfies readonly { id: string; label: string; states: readonly InboxState[] }[]

export type InboxTab = (typeof TABS)[number]['id']

export const PAGE_SIZE = 50

/** The largest file the inbox takes (`SIZE_MAX`, 100 MiB): refused here before the bytes leave. */
export const MAX_FILE_BYTES = 100 * 1024 * 1024

/** What the file picker offers. Only a hint, like every `accept` here: the server reads the
 *  bytes and decides. */
export const ACCEPT =
  '.xml,.p7m,.zip,.pdf,.png,.jpg,.jpeg,.csv,.xls,.xlsx,application/pdf,image/png,image/jpeg,application/zip,application/xml,text/xml'

/** Kinds that carry a legal entity and so can wait for a person to choose it. */
const NEEDS_COMPANY: Record<InboxKind, boolean> = {
  sales_invoice: true,
  supplier_invoice: true,
  credit_note: true,
  receipt: true,
  bank_statement: true,
  tax_document: true,
  payslip: true,
  contract: false,
  archive: false,
  other: false,
}

/** An item nobody has said what it is yet and that still has its file to read. */
export function isUnread(item: Pick<InboxItem, 'state' | 'kind' | 'has_file'>): boolean {
  return item.state === 'new' && item.kind === null && item.has_file
}

/** Waiting for a person to choose the company it is for. */
export function needsCompany(item: Pick<InboxItem, 'state' | 'kind' | 'legal_entity_id'>): boolean {
  return (
    item.state === 'new' &&
    item.kind !== null &&
    item.legal_entity_id === null &&
    NEEDS_COMPANY[item.kind]
  )
}

/** The word of the «Tipo» column. */
export function kindWord(item: Pick<InboxItem, 'state' | 'kind' | 'has_file'>): string {
  if (item.kind !== null) return KIND_LABELS[item.kind]
  return isUnread(item) ? 'Da leggere' : 'Non riconosciuto'
}

/**
 * The one line under a state's pill, saying why the item stands where it does: the company to
 * choose first, then the sentence the server wrote (the reader's, the refusal's, the person's),
 * then a default per state.
 */
export function whyLine(item: InboxItem): string {
  if (needsCompany(item)) return 'Azienda da scegliere'
  if (item.reason !== null && item.reason !== '') return item.reason
  switch (item.state) {
    case 'new':
      return isUnread(item) ? 'Aspetta di essere letto' : 'In attesa di una persona'
    case 'proposed':
      return 'Letto dal modello, aspetta la tua conferma'
    case 'filed':
      return 'Registrato'
    case 'duplicate':
      return 'Stesso file di uno già arrivato'
    case 'discarded':
      return 'Scartato'
  }
}

/** The brief's «Solo tu» line: who sees an item that has no company yet. */
export function visibilityLine(item: InboxItem, me: string | null, admin: boolean): string | null {
  if (item.legal_entity_id !== null) return null
  if (admin) {
    return item.uploaded_by_id === me
      ? 'Visibile solo a te e agli amministratori'
      : 'Visibile solo a chi l’ha caricato e agli amministratori'
  }
  return 'Visibile a te e agli amministratori finché non ha un’azienda'
}

const dateFormatter = new Intl.DateTimeFormat('it-IT', {
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
})

/** `02/11/2026`. */
export function formatDay(iso: string): string {
  return dateFormatter.format(new Date(iso))
}

const BYTES_PER_KILOBYTE = 1024

/** A file's size as Finder writes it: «12,1 kB», never a unit above the megabyte. */
export function formatSize(bytes: number): string {
  const big = bytes >= BYTES_PER_KILOBYTE
  return new Intl.NumberFormat('it-IT', {
    style: 'unit',
    unit: big ? 'kilobyte' : 'byte',
    maximumFractionDigits: 1,
  }).format(big ? bytes / BYTES_PER_KILOBYTE : bytes)
}

/** «1 documento» / «3 documenti». */
export function documents(count: number): string {
  return count === 1 ? '1 documento' : `${count} documenti`
}
