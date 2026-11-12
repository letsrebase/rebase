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
 * `lib/dates.ts` and `features/dashboard/period.ts` exist as modules of their own.
 */

import { KIND_FALLBACK_LABELS, PAYLOAD_KEY_LABELS, PAYLOAD_VALUE_LABELS } from './payloadLabels'

const KIND_LABELS: Record<string, string> = {
  created: 'Creato',
  updated: 'Modificato',
  deleted: 'Archiviato',
  restored: 'Ripristinato',
  // REB-995: two organizations of one company folded into one.
  merged: "Un'anagrafica è stata unita a questa",
  merged_into: "Unita in un'altra anagrafica",
  stage_changed: 'Cambio stato',
  state_changed: 'Cambio stato',
  stopped: 'Terminata',
  // Slice 9's invoice import. Named here rather than left to `humanize`, which would
  // render the English "Imported" on an Italian timeline, and phrased as what happened
  // to the document -- it was issued elsewhere and registered here -- without naming the
  // tool it came out of: the provenance value is the CRM's own record, not copy (see
  // `invoices/models.py::Invoice.imported_from`).
  imported: 'Fattura importata',
  // A bank statement's own history (REB-1025): `imported` above is its import too, read on the
  // account; the account's `created`, `updated` and `archived` are the plain words.
  reverted: 'Importazione annullata',
  // An inbox item a person registered as a purchase (REB-1023): the entry on the item.
  filed: 'Registrato come acquisto',
  // A contract's own history (REB-741..744): what each write on its page records.
  rate_card_added: 'Tariffa aggiunta',
  renewed: 'Contratto rinnovato',
  converted_to_contract: 'Convertito in contratto',
  clause_note_added: 'Nota su una clausola aggiunta',
  clause_note_updated: 'Nota su una clausola modificata',
  rate_card_updated: 'Tariffa modificata',
  // A contract's recurring fee billed on a draft invoice (REB-1032).
  fee_billed: 'Canone fatturato in bozza',
  expense_added: 'Spesa registrata',
  expense_updated: 'Spesa modificata',
  renewal_assumption_set: 'Assunzione di rinnovo salvata',
  // A contract's day ledger (REB-781): who recorded the client's approval of days, and who
  // linked one to a day that did not change state, which the day's own ledger does not show.
  approval_added: 'Approvazione aggiunta',
  approval_recorded: 'Approvazione registrata',
  approval_linked: 'Approvazione collegata a una giornata',
  // The notes thread of a deal, a customer or a person (REB-879): the text itself stays in
  // the thread, the timeline only says that it moved.
  note_added: 'Nota aggiunta',
  note_edited: 'Nota modificata',
  note_deleted: 'Nota eliminata',
  // The mail drafts of a record and the Slack settings, the other writes that used to leave
  // no row at all. `gmail.message_sent` and its siblings fall to `humanize`, as before.
  'gmail.draft_created': 'Bozza di email creata',
  'gmail.draft_edited': 'Bozza di email modificata',
  'gmail.draft_deleted': 'Bozza di email eliminata',
  // A token's full access opened or closed (REB-810), recorded on its owner's account.
  pat_full_access_changed: 'Accesso completo di un token modificato',
  'slack.manual_match': 'Utente Slack abbinato a mano',
  'slack.match_removed': 'Abbinamento Slack rimosso',
  'slack.matches_rebuilt': 'Abbinamenti Slack rifatti',
  'slack.channel_set': 'Canale Slack impostato',
  'slack.channel_removed': 'Canale Slack tolto',
  'slack.workspace_connected': 'Workspace Slack collegato',
  'slack.workspace_disconnected': 'Workspace Slack scollegato',
  // An IMAP or PEC mailbox (REB-1015): who connected it, changed its password or disconnected it.
  'mailbox.connected': 'Casella email collegata',
  'mailbox.password_changed': 'Password della casella email cambiata',
  'mailbox.disconnected': 'Casella email scollegata',
  // A contract's days (REB-781): the day's own ledger keeps the transitions by id, and these
  // are the same moves with the member's name and the channel.
  // «In arrivo» (REB-984): what a person or an agent said about a file that arrived.
  discarded: 'Documento scartato',
  marked_duplicate: 'Documento segnato come doppione',
  kind_set: 'Tipo del documento indicato',
  day_created: 'Giornata registrata',
  day_state_changed: 'Giornata cambiata di stato',
  // The month's report of an engagement (REB-1002): received, then approved by a person.
  monthly_report_requested: 'Rendiconto mensile ricevuto',
  monthly_report_approved: 'Rendiconto mensile approvato',
  // A letter's signature (REB-1019): out for signature, signed, cancelled, the mail sent again, a
  // notice recorded on a framework agreement, and a signed PDF uploaded when there is no Documenso.
  signing_sent: 'Inviato per la firma',
  signing_signed: 'Firmato',
  signing_cancelled: 'Firma annullata',
  signing_mail_resent: 'Mail per la firma rimandata',
  notice_recorded: 'Disdetta registrata',
  signed_copy_uploaded: 'PDF firmato caricato',
  // The hub's company requests (REB-866): a request filed as a new deal, and one linked by
  // an admin to a deal the CRM already had.
  'hub.request_received': "Richiesta dall'hub ricevuta",
  'hub.request_linked': "Collegata la richiesta dall'hub",
  // The space's reminder defaults, the only part of the reminder settings that is not one
  // person's own.
  defaults_updated: 'Promemoria: valori predefiniti modificati',
  defaults_reset: 'Promemoria: valori predefiniti ripristinati',
  // The rest of what the core records, named for the same reason (REB-901).
  opened: 'Invito aperto',
  accepted: 'Accettata',
  rejected: 'Rifiutata',
  archived: 'Archiviato',
  unarchived: 'Ripristinato',
  activated: 'Attivato',
  deactivated: 'Disattivato',
  closed: 'Mese chiuso',
  created_from_template: 'Creato da un template',
  letter_created: 'Lettera scritta',
  day_approved: 'Giornata approvata',
  email_template_saved: 'Modello di email salvato',
  gap_declared: 'Numero mancante dichiarato',
  invited: 'Invitato',
  resent: 'Invito rinviato',
  revoked: 'Invito revocato',
  password_reset: 'Password reimpostata',
  pat_created: 'Token creato',
  legal_entity_site_added: 'Sede aggiunta',
  legal_entity_site_updated: 'Sede modificata',
  legal_entity_site_removed: 'Sede rimossa',
  staff_member_created: "Persona dell'azienda aggiunta",
  staff_member_updated: "Persona dell'azienda modificata",
  payroll_pay_day_set: 'Giorno di paga impostato',
  staff_payslip_recorded: 'Cedolino registrato',
  staff_payslip_corrected: 'Cedolino corretto',
  expense_note_submitted: 'Nota spese inviata',
  expense_note_approved: 'Nota spese approvata',
  expense_note_rejected: 'Nota spese rifiutata',
  expense_note_way_chosen: 'Modo di rimborso scelto',
  expense_note_reimbursed: 'Nota spese rimborsata',
  payroll_month_closed: 'Mese di paga chiuso',
  declared_tax_figure_set: 'Cifre della dichiarazione registrate',
  tax_loss_set: 'Perdita fiscale registrata',
  tax_loss_deleted: 'Perdita fiscale tolta',
  tax_advance_method_set: 'Metodo degli acconti scelto',
  tax_carry_confirmed: 'Differenza sull’acconto confermata',
  tax_instalment_plan_set: 'Rateazione delle imposte scelta',
  tax_notice_plan_created: 'Piano di rate di un avviso registrato',
  tax_notice_plan_updated: 'Piano di rate di un avviso cambiato',
  tax_notice_plan_deleted: 'Piano di rate di un avviso eliminato',
  f24_grouped: 'Scadenze raggruppate in un F24',
  f24_ungrouped: 'F24 sciolto',
  f24_settled: 'F24 pagato',
  f24_filed: 'F24 presentato',
  f24_reopened: 'F24 riaperto',
  f24_receipt_attached: 'Ricevuta F24 collegata',
  f24_receipt_detached: 'Ricevuta F24 scollegata',
  accountant_export_built: 'Esportazione per il commercialista preparata',
  accountant_export_deleted: 'Esportazione per il commercialista eliminata',
  accountant_export_written_to_drive: 'Esportazione scritta su Google Drive',
  export_share_link_created: 'Link per il commercialista creato',
  export_share_link_revoked: 'Link per il commercialista revocato',
  iva_period_settled: 'IVA del periodo liquidata',
  iva_period_closed: 'Periodo IVA chiuso',
  iva_period_reopened: 'Periodo IVA riaperto',
  pat_first_used: 'Token usato per la prima volta',
  pat_revoked: 'Token revocato',
  pat_used_after_revocation: 'Token usato dopo la revoca',
  rate_updated: 'Tariffa del deal modificata',
  rates_updated: 'Tariffe modificate',
  rates_recalculated: 'Tariffe ricalcolate',
  time_logged_on_closed_deal: 'Ore registrate su un deal chiuso',
  version_added: 'Versione aggiunta',
  // An invoice's own history: what each step from draft to register records. Named here
  // because `humanize` would print the English word on an Italian timeline.
  lines_replaced: 'Righe sostituite',
  confirmed: 'Confermata',
  issued: 'Emessa',
  annulled: 'Annullata',
  payment_state_changed: 'Stato di pagamento cambiato',
  transmitted_externally: 'Trasmessa esternamente',
  time_bound_to_invoice: 'Ore collegate a una fattura',
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
  return KIND_LABELS[kind] ?? KIND_FALLBACK_LABELS[kind] ?? humanize(kind)
}

/** A payload key (or a field name in `changed`) in the Italian word it was stored as before the rename. */
export function labelForPayloadKey(key: string): string {
  return Object.hasOwn(PAYLOAD_KEY_LABELS, key) ? PAYLOAD_KEY_LABELS[key]! : humanize(key)
}

/**
 * A payload value as the timeline prints it: a coded value goes back to its Italian word, any
 * other as it is. A list (the `keys` of a settings change) is read item by item and joined the
 * way `String(list)` always joined it.
 */
export function labelForPayloadValue(kind: string, key: string, value: unknown): string {
  const table = Object.hasOwn(PAYLOAD_VALUE_LABELS, kind) ? PAYLOAD_VALUE_LABELS[kind]![key] : undefined
  const word = (item: unknown): string => {
    const text = String(item)
    return table !== undefined && Object.hasOwn(table, text) ? table[text]! : text
  }
  return Array.isArray(value) ? value.map(word).join(',') : word(value)
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
