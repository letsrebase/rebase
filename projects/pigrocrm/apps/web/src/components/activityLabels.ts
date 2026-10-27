/**
 * The labels an activity row is read by, and the one instant formatter it needs.
 *
 * Extracted from `Timeline.tsx` when a second reader appeared: the operational dashboard's
 * "attività recenti" (§6.1) renders the same `ActivityRead` rows in a much shorter form,
 * and a second `KIND_LABELS` table is how two screens start calling the same event two
 * different things. `Timeline.tsx` still owns everything that is about *its* rendering --
 * the actor badge, the payload detail -- because none of that is shared.
 *
 * A plain module, not a `.tsx`: it exports no component, so there is no
 * `react-refresh/only-export-components` override to buy it. That is the same reason
 * `lib/dates.ts` and `features/dashboard/periodo.ts` exist as modules of their own.
 */

const KIND_LABELS: Record<string, string> = {
  created: 'Creato',
  updated: 'Modificato',
  deleted: 'Archiviato',
  restored: 'Ripristinato',
  stage_changed: 'Cambio stato',
  state_changed: 'Cambio stato',
  // Slice 9's invoice import. Named here rather than left to `humanize`, which would
  // render the English "Imported" on an Italian timeline, and phrased as what happened
  // to the document -- it was issued elsewhere and registered here -- without naming the
  // tool it came out of: the provenance value is the CRM's own record, not copy (see
  // `invoices/models.py::Invoice.importata_da`).
  imported: 'Fattura importata',
  // A contract's own history (REB-741..744): what each write on its page records.
  rate_card_added: 'Tariffa aggiunta',
  renewed: 'Contratto rinnovato',
  clause_note_added: 'Nota su una clausola aggiunta',
  clause_note_updated: 'Nota su una clausola modificata',
  rate_card_updated: 'Tariffa modificata',
  expense_added: 'Spesa registrata',
  expense_updated: 'Spesa modificata',
  renewal_assumption_set: 'Assunzione di rinnovo salvata',
  // A contract's day ledger (REB-781): who recorded the client's approval of days, and who
  // linked one to a day that did not change state, which the day's own ledger does not show.
  approval_added: 'Approvazione aggiunta',
  approval_recorded: 'Approvazione registrata',
  approval_linked: 'Approvazione collegata a una giornata',
  // The mail drafts of a record and the Slack settings, the other writes that used to leave
  // no row at all. `gmail.messaggio_inviato` and its siblings fall to `humanize`, as before.
  'gmail.bozza_creata': 'Bozza di email creata',
  'gmail.bozza_modificata': 'Bozza di email modificata',
  'gmail.bozza_eliminata': 'Bozza di email eliminata',
  'slack.abbinamento_manuale': 'Utente Slack abbinato a mano',
  'slack.abbinamento_rimosso': 'Abbinamento Slack rimosso',
  'slack.abbinamenti_rifatti': 'Abbinamenti Slack rifatti',
  'slack.canale_impostato': 'Canale Slack impostato',
}

/**
 * The entity an activity is about, in the words the rest of the interface uses.
 *
 * Only the dashboard needs this: the timeline is already inside the entity it belongs to,
 * so there is nothing there for it to say. Unknown values fall through to `humanize` for
 * the reason given below -- `activities/models.py` promises new `entity_type` values will
 * arrive without a migration.
 */
const ENTITY_LABELS: Record<string, string> = {
  customer: 'Cliente',
  person: 'Persona',
  deal: 'Deal',
  document: 'Documento',
  invoice: 'Fattura',
  time_entry: 'Ore',
  cost: 'Costo',
  email_draft: 'Email',
}

/**
 * Turns a snake_case value this build has no specific label for into a sentence-case
 * phrase -- e.g. a future `email_received` reads as "Email received" instead of `undefined`
 * or a blank cell. Only the first word is capitalised, matching Italian convention (and
 * `KIND_LABELS` above: "Cambio stato", not "Cambio Stato") rather than English-style Title
 * Case, which would read as visibly foreign next to every hand-written label around it.
 * Not a translation (the source values are already a mix of English and Italian domain
 * words), only formatting: the one honest thing to do with a value nobody taught this
 * build about yet.
 */
export function humanize(value: string): string {
  const [first, ...rest] = value.split('_').filter(Boolean)
  if (!first) return '—'
  return [first.charAt(0).toUpperCase() + first.slice(1), ...rest].join(' ')
}

export function labelForKind(kind: string): string {
  return KIND_LABELS[kind] ?? humanize(kind)
}

export function labelForEntityType(entityType: string): string {
  return ENTITY_LABELS[entityType] ?? humanize(entityType)
}

const dateTimeFormatter = new Intl.DateTimeFormat('it-IT', {
  dateStyle: 'medium',
  timeStyle: 'short',
})

// The timeline answers «when exactly did this member change it», which a minute does not
// when two members edit the same record in one sitting: seconds on top of `dateTimeFormatter`.
const exactDateTimeFormatter = new Intl.DateTimeFormat('it-IT', {
  dateStyle: 'medium',
  timeStyle: 'medium',
})

/**
 * `occurred_at` is a full ISO-8601 datetime with an explicit UTC offset (verified live
 * against the real API: `"2026-08-07T20:26:33.337592Z"`), never a bare "YYYY-MM-DD" -- so,
 * unlike a date-only field (see `lib/dates.ts` and its own docstring on exactly this trap),
 * handing it straight to `new Date(...)` is safe: there is no local-midnight ambiguity to
 * lose a day over, only a real moment in time that `Intl.DateTimeFormat` then renders in
 * the browser's own zone.
 */
export function formatOccurredAt(occurredAt: string): string {
  return dateTimeFormatter.format(new Date(occurredAt))
}

/** `formatOccurredAt` with the seconds, for the one screen that is about the exact moment. */
export function formatOccurredAtExact(occurredAt: string): string {
  return exactDateTimeFormatter.format(new Date(occurredAt))
}
