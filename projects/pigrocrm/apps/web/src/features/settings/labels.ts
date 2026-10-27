/**
 * What the settings screens call the keys the API stores.
 *
 * Each of these is a closed set the backend owns (`TemplateTipo`, `AttivitaRule`, the seeded
 * cost categories), written as a key because a key is what code and reports match on. A
 * person reads the Italian name, never the key. A key this file has not heard of, because
 * the backend grew one before the client did, is shown humanised (`software_licenze` as
 * «Software licenze») and not blank, which would hide the row.
 */

/** `viaggi_trasferte` as «Viaggi trasferte»: the fallback for a key with no entry. */
export function humaniseKey(key: string): string {
  const spaced = key.replace(/_/g, ' ').trim()
  return spaced.charAt(0).toLocaleUpperCase('it-IT') + spaced.slice(1)
}

/** The rule a run of the automations was written by: the title its own card carries, short. */
export const RULE_LABELS: Record<string, string> = {
  A1: 'Offerta accettata',
  A2: 'Offerta inviata',
  offerta_inviata_richiama: 'Offerta senza risposta',
  deal_fermo: 'Deal fermo',
  fattura_scaduta: 'Fattura scaduta',
}

export function ruleLabel(regola: string): string {
  return (Object.hasOwn(RULE_LABELS, regola) ? RULE_LABELS[regola] : undefined) ?? humaniseKey(regola)
}

/** `Template.tipo`: the four kinds a person creates and the three the product ships. */
export const TEMPLATE_TYPE_LABELS: Record<string, string> = {
  offerta: 'Offerta',
  contratto: 'Contratto',
  verbale: 'Verbale',
  documento: 'Documento',
  rapporto_ore: 'Rapporto ore',
  email: 'Email',
  sollecito: 'Sollecito',
}

export function templateTypeLabel(tipo: string): string {
  return (Object.hasOwn(TEMPLATE_TYPE_LABELS, tipo) ? TEMPLATE_TYPE_LABELS[tipo] : undefined) ?? humaniseKey(tipo)
}

/** The `code` of a seeded cost category (`SEED_CATEGORIES`), which reports match on. */
export const COST_CATEGORY_CODE_LABELS: Record<string, string> = {
  consulenza_esterna: 'Consulenza esterna',
  software_licenze: 'Software e licenze',
  viaggi_trasferte: 'Viaggi e trasferte',
  materiali: 'Materiali',
  altro: 'Altro',
}

export function costCategoryCodeLabel(code: string): string {
  return (Object.hasOwn(COST_CATEGORY_CODE_LABELS, code) ? COST_CATEGORY_CODE_LABELS[code] : undefined) ?? humaniseKey(code)
}
