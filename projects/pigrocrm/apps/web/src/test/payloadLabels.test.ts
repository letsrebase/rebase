/**
 * The timeline's Italian words against their source, migration 0068.
 *
 * 0068 rewrote the persisted vocabulary (`bozza` -> `draft`, `attivo` -> `active`, ...) and the
 * activity payloads with it, and the timeline has to keep printing the word a person read before.
 * `components/payloadLabels.ts` is that table. This reads the migration's own old-to-new maps and
 * fails when:
 *
 *  1. a renamed value of a payload key the timeline prints does not resolve to its old Italian
 *     word (`KIND_VALUES`, the maps 0068 applied to the stored payloads);
 *  2. a renamed column vocabulary (`COLUMN_VALUES`) is neither bound to the payload keys that
 *     carry it nor listed below as one the timeline never prints, so a vocabulary added to the
 *     migration cannot slip past unread;
 *  3. a column renamed on an entity with a timeline has no Italian word as a field name.
 *
 * The migration is Python; its maps are plain literals, read here with a small literal parser
 * rather than a second copy of them.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { labelForPayloadKey, labelForPayloadValue } from '@/components/activityLabels'
import { PAYLOAD_KEY_LABELS } from '@/components/payloadLabels'
import { DOCUMENT_TYPE_LABELS } from '@/features/documents/queries'

const MIGRATION = join(
  __dirname,
  '..',
  '..',
  '..',
  '..',
  'packages',
  'core',
  'migrations',
  'versions',
  '0068_english_names.py',
)

type Literal = string | null | boolean | number | Literal[] | { [key: string]: Literal }
/** A Python tuple, kept apart from a list so a `("table", "column")` key can be told from a value. */
class Tuple {
  constructor(readonly items: Literal[]) {}
}
type Parsed = Literal | Tuple | Map<string | Tuple, Parsed>

/** A recursive-descent reader for the literals of the migration: str, None, tuple, list, dict. */
class LiteralReader {
  private pos = 0
  constructor(private readonly text: string) {}

  private skip(): void {
    for (;;) {
      const char = this.text[this.pos]
      if (char === '#') {
        while (this.pos < this.text.length && this.text[this.pos] !== '\n') this.pos++
      } else if (char !== undefined && /\s/.test(char)) {
        this.pos++
      } else return
    }
  }

  private expect(char: string): void {
    this.skip()
    if (this.text[this.pos] !== char) {
      throw new Error(`expected ${char} at ${this.text.slice(this.pos, this.pos + 40)}`)
    }
    this.pos++
  }

  private string(): string {
    const quote = this.text[this.pos]
    this.pos++
    let out = ''
    while (this.text[this.pos] !== quote) {
      const char = this.text[this.pos]!
      if (char === '\\') {
        this.pos++
        out += this.text[this.pos]
      } else out += char
      this.pos++
    }
    this.pos++
    return out
  }

  private sequence(close: string): Parsed[] {
    const items: Parsed[] = []
    for (;;) {
      this.skip()
      if (this.text[this.pos] === close) {
        this.pos++
        return items
      }
      items.push(this.value())
      this.skip()
      if (this.text[this.pos] === ',') this.pos++
    }
  }

  value(): Parsed {
    this.skip()
    const char = this.text[this.pos]!
    if (char === '"' || char === "'") {
      // Adjacent literals are one string in Python.
      let out = this.string()
      for (;;) {
        this.skip()
        const next = this.text[this.pos]
        if (next !== '"' && next !== "'") return out
        out += this.string()
      }
    }
    if (char === '[') {
      this.pos++
      return this.sequence(']') as Literal[]
    }
    if (char === '(') {
      this.pos++
      return new Tuple(this.sequence(')') as Literal[])
    }
    if (char === '{') {
      this.pos++
      const out = new Map<string | Tuple, Parsed>()
      for (;;) {
        this.skip()
        if (this.text[this.pos] === '}') {
          this.pos++
          return out
        }
        const key = this.value() as string | Tuple
        this.expect(':')
        out.set(key, this.value())
        this.skip()
        if (this.text[this.pos] === ',') this.pos++
      }
    }
    const word = /^[A-Za-z0-9_.-]+/.exec(this.text.slice(this.pos))
    if (word === null) throw new Error(`unreadable literal at ${this.text.slice(this.pos, this.pos + 40)}`)
    this.pos += word[0].length
    if (word[0] === 'None') return null
    if (word[0] === 'True') return true
    if (word[0] === 'False') return false
    return Number(word[0])
  }
}

const source = readFileSync(MIGRATION, 'utf8')

function constant(name: string): Parsed {
  const match = new RegExp(`^${name} = `, 'm').exec(source)
  if (match === null) throw new Error(`${name} is not in the migration`)
  return new LiteralReader(source.slice(match.index + match[0].length)).value()
}

function asMap(value: Parsed): Map<string | Tuple, Parsed> {
  if (!(value instanceof Map)) throw new Error('expected a dict')
  return value
}

function words(value: Parsed): Array<[string, string]> {
  return [...asMap(value).entries()].map(([old, now]) => [old as string, now as string])
}

const KIND_VALUES = asMap(constant('KIND_VALUES'))
const KIND_KEYS = asMap(constant('KIND_KEYS'))
const COLUMN_VALUES = asMap(constant('COLUMN_VALUES'))
const COLUMNS_BY_OLD_TABLE = asMap(constant('COLUMNS_BY_OLD_TABLE'))
const ACTIVITY_KINDS = (constant('ACTIVITY_KINDS') as Parsed[]).map((row) =>
  (row as Tuple).items.map((item) => item as string | null),
)

const columnId = (key: string | Tuple): string => (key as Tuple).items.join('.')

describe('the timeline words against migration 0068', () => {
  it('reads the migration', () => {
    expect(KIND_VALUES.size).toBeGreaterThan(10)
    expect(COLUMN_VALUES.size).toBeGreaterThan(30)
  })

  it('prints the old Italian word for every renamed value 0068 rewrote in a payload', () => {
    let checked = 0
    for (const [oldKind, byKey] of KIND_VALUES) {
      const kind = ACTIVITY_KINDS.find((row) => row[2] === oldKind)?.[3] ?? (oldKind as string)
      const keys = KIND_KEYS.get(oldKind)
      for (const [oldKey, vocabulary] of asMap(byKey)) {
        const key = keys instanceof Map ? ((keys.get(oldKey) as string | undefined) ?? oldKey) : oldKey
        for (const [old, now] of words(vocabulary)) {
          expect(old, `${kind}.${key}: ${now} was renamed`).not.toBe(now)
          expect(labelForPayloadValue(kind as string, key as string, now), `${kind}.${key}=${now}`).toBe(old)
          checked++
        }
      }
    }
    expect(checked).toBeGreaterThan(80)
  })

  /**
   * Where each column vocabulary of 0068 reaches a timeline line: the `[kind, key]` payload
   * entries that the core writes it into (`activities.record(...)` in `packages/core`).
   */
  const stateChanged: Array<[string, string]> = [
    ['state_changed', 'from'],
    ['state_changed', 'to'],
  ]
  const noteSource: Array<[string, string]> = ['note_added', 'note_edited', 'note_deleted'].map(
    (kind): [string, string] => [kind, 'source'],
  )
  const fieldEntity: Array<[string, string]> = ['created', 'archived', 'unarchived'].map(
    (kind): [string, string] => [kind, 'entity_type'],
  )
  const PRINTED: Record<string, Array<[string, string]>> = {
    'tasks.rule': [
      ['automation.task_opened', 'rule'],
      ['automation.stage_moved', 'rule'],
      ['automation.not_executed', 'rule'],
    ],
    'approvals.channel': [
      ['approval_added', 'channel'],
      ['approval_recorded', 'channel'],
    ],
    'contracts.status': stateChanged,
    'rate_cards.type': [['rate_card_added', 'type']],
    'documents.type': [
      ['created', 'type'],
      ['document.imported', 'type'],
    ],
    'documents.status': stateChanged,
    'invoices.type': [['created', 'type']],
    'invoices.payment_status': [['payment_state_changed', 'payment_status']],
    'invoices.imported_from': [['imported', 'imported_from']],
    'notes.source': noteSource,
    'proposals.target_type': [['created', 'target_type']],
    'time_entries.rate_source': [['created', 'rate_source']],
    'work_units.status': [
      ['day_created', 'status'],
      ['day_state_changed', 'from'],
      ['day_state_changed', 'to'],
    ],
    'users.role': [['created', 'role']],
    'invitations.role': [['created', 'role']],
    'pipeline_stages.code': [
      ['created', 'code'],
      ['deleted', 'code'],
      ['updated', 'code'],
    ],
    'cost_categories.code': [['created', 'code']],
    'space_settings.key': [['updated', 'keys']],
    'field_definitions.entity_type': fieldEntity,
    'reminder_preference.type': [
      ['defaults_updated', 'type'],
      ['defaults_reset', 'type'],
    ],
  }
  /** Renamed vocabularies no timeline line prints, each with the reason. */
  const NOT_PRINTED: Record<string, string> = {
    'tasks.status': 'a task row carries no status in its payload; its kind is the event',
    'tasks.origin': 'a task is created with a title and a due date only',
    'contracts.renewal_type': 'only in the proposal fields, which the proposal page reads',
    'rate_cards.unit': 'rate card events name the type and the dates',
    'rate_cards.delivery_period': 'rate card events name the type and the dates',
    'templates.type': 'a template is created with its name only',
    'invoices.status': 'the invoice kinds (`confirmed`, `issued`...) are the status events',
    'note_revisions.source': 'revisions are read in the note thread, not the timeline',
    'proposals.excerpt_type': 'on the proposal page only',
    'proposals.status': 'the proposal kinds (`accepted`, `rejected`) are the status events',
    'time_entries.cost_source': 'a time entry is created with its rate source only',
    'work_unit_transitions.previous_status': 'the day ledger reads it through features/days/labels',
    'work_unit_transitions.new_status': 'the day ledger reads it through features/days/labels',
    'email_drafts.send_state': 'draft events carry the draft id',
    'reminder_ack.type': 'a person’s own reminder state is not recorded',
    'reminder_snooze.type': 'a person’s own reminder state is not recorded',
    'reminder_delivery.type': 'a person’s own reminder state is not recorded',
  }

  it('accounts for every renamed column vocabulary: bound to a printed key or not printed at all', () => {
    const migrated = [...COLUMN_VALUES.keys()].map(columnId).sort()
    const accounted = [...Object.keys(PRINTED), ...Object.keys(NOT_PRINTED)].sort()
    expect(accounted).toEqual(migrated)
  })

  it('prints the old Italian word for every renamed value of every column the timeline prints', () => {
    for (const [column, bindings] of Object.entries(PRINTED)) {
      const entry = [...COLUMN_VALUES.entries()].find(([key]) => columnId(key) === column)
      expect(entry, column).toBeDefined()
      for (const [old, now] of words(entry![1])) {
        for (const [kind, key] of bindings) {
          expect(labelForPayloadValue(kind, key, now), `${column}: ${kind}.${key}=${now}`).toBe(old)
        }
      }
    }
  })

  it('reads a contract moving between its states in the words the contract screens were stored with', () => {
    expect(labelForPayloadValue('state_changed', 'to', 'active')).toBe('attivo')
    expect(labelForPayloadValue('state_changed', 'from', 'draft')).toBe('bozza')
    expect(labelForPayloadValue('state_changed', 'to', 'terminated')).toBe('terminato')
    expect(labelForPayloadValue('state_changed', 'to', 'expired')).toBe('scaduto')
    expect(labelForPayloadValue('state_changed', 'to', 'accepted')).toBe('accettata')
  })

  it('reads a list item by item and leaves a word it has no row for as it is', () => {
    expect(
      labelForPayloadValue('updated', 'keys', [
        'payment_reminders_grace_days',
        'preferred_concentration_threshold',
        'unheard_of',
      ]),
    ).toBe('solleciti_grace_days,concentrazione_soglia_preferita,unheard_of')
    expect(labelForPayloadValue('created', 'type', 'unheard_of')).toBe('unheard_of')
    expect(labelForPayloadValue('unheard_of', 'type', 'invoice')).toBe('invoice')
    expect(labelForPayloadValue('created', 'title', 'invoice')).toBe('invoice')
  })

  /**
   * Field names: the `changed` list and the generic detail line print a payload key as a
   * word, and a key without one falls to `humanize`, which prints its English spelling.
   * Every column renamed on an entity with a timeline must have its word.
   */
  const TIMELINE_TABLES = [
    'customers',
    'people',
    'deals',
    'documents',
    'invoices',
    'contracts',
    'attivita',
    'costs',
    'time_entries',
    'rate_cards',
    'contract_expenses',
    'clause_notes',
    'renewal_assumptions',
    'users',
    'invitations',
    'pipeline_stages',
    'cost_categories',
    'templates',
    'emitter_profile',
    'fiscal_profile',
    'work_units',
    'period_locks',
    'proposals',
  ]

  it('has an Italian word for every column renamed on an entity with a timeline', () => {
    for (const table of TIMELINE_TABLES) {
      const columns = COLUMNS_BY_OLD_TABLE.get(table)
      expect(columns, `${table} is a table of the migration`).toBeDefined()
      for (const [old, now] of words(columns!)) {
        if (old === now || now.endsWith('_id') || now === 'closed_by') continue
        expect(Object.hasOwn(PAYLOAD_KEY_LABELS, now), `${table}.${now}`).toBe(true)
      }
    }
  })

  it('names the deal stage and the custom fields in Italian', () => {
    expect(labelForPayloadKey('stage')).toBe('Stato')
    expect(labelForPayloadKey('custom_fields')).toBe('Campi personalizzati')
  })

  it('writes the accents of the field names', () => {
    expect(labelForPayloadKey('probability')).toBe('Probabilità')
    expect(labelForPayloadKey('default_probability')).toBe('Probabilità default')
    expect(labelForPayloadKey('payment_method')).toBe('Modalità pagamento')
    expect(labelForPayloadKey('profitability_coefficient')).toBe('Coefficiente redditività')
  })

  it('gives every document type a word that is not the code', () => {
    for (const [, now] of words(
      [...COLUMN_VALUES.entries()].find(([key]) => columnId(key) === 'documents.type')![1],
    )) {
      expect(DOCUMENT_TYPE_LABELS[now], now).toBeDefined()
      expect(DOCUMENT_TYPE_LABELS[now]).not.toBe(now)
    }
    expect(DOCUMENT_TYPE_LABELS.proforma).toBe('Proforma')
    expect(DOCUMENT_TYPE_LABELS.invoice).toBe('Fattura')
    expect(DOCUMENT_TYPE_LABELS.invoice_xml).toBe('Fattura XML')
  })
})
