/**
 * What the settings screens call the keys the API stores.
 *
 * Each of these is a closed set the backend owns (`TemplateType`, `TaskRule`, the seeded
 * cost categories), written as a key because a key is what code and reports match on. A
 * person reads the Italian name, never the key. A key this file has not heard of, because
 * the backend grew one before the client did, is shown humanised (`software_licenses` as
 * «Software licenze») and not blank, which would hide the row.
 */

/** `travel` as «Viaggi trasferte»: the fallback for a key with no entry. */
export function humaniseKey(key: string): string {
  const spaced = key.replace(/_/g, ' ').trim()
  return spaced.charAt(0).toLocaleUpperCase('it-IT') + spaced.slice(1)
}

/** The rule a run of the automations was written by: the title its own card carries, short. */
export const RULE_LABELS: Record<string, string> = {
  A1: 'Offerta accettata',
  A2: 'Offerta inviata',
  sent_offer_follow_up: 'Offerta senza risposta',
  stalled_deal: 'Deal fermo',
  overdue_invoice: 'Fattura scaduta',
}

export function ruleLabel(rule: string): string {
  return (Object.hasOwn(RULE_LABELS, rule) ? RULE_LABELS[rule] : undefined) ?? humaniseKey(rule)
}

/** `Template.type`: the four kinds a person creates and the three the product ships. */
export const TEMPLATE_TYPE_LABELS: Record<string, string> = {
  offer: 'Offerta',
  contract: 'Contratto',
  minutes: 'Verbale',
  document: 'Documento',
  hours_report: 'Rapporto ore',
  framework_agreement: 'Contratto quadro',
  engagement_letter: 'Lettera di incarico',
  email: 'Email',
  payment_reminder: 'Sollecito',
}

export function templateTypeLabel(type: string): string {
  return (Object.hasOwn(TEMPLATE_TYPE_LABELS, type) ? TEMPLATE_TYPE_LABELS[type] : undefined) ?? humaniseKey(type)
}

/** The `code` of a seeded cost category (`SEED_CATEGORIES`), which reports match on. */
export const COST_CATEGORY_CODE_LABELS: Record<string, string> = {
  external_consulting: 'Consulenza esterna',
  software_licenses: 'Software e licenze',
  travel: 'Viaggi e trasferte',
  materials: 'Materiali',
  other: 'Altro',
}

export function costCategoryCodeLabel(code: string): string {
  return (Object.hasOwn(COST_CATEGORY_CODE_LABELS, code) ? COST_CATEGORY_CODE_LABELS[code] : undefined) ?? humaniseKey(code)
}
