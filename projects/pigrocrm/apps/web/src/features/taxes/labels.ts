import type { components } from '@/lib/api-types'

/**
 * The words of «Tasse» (REB-1026, the approved brief `docs/design/2026-11-10-taxes-brief.md`).
 * Each word of a reading is written once and read from here on every screen: the Home's block,
 * the page, the panel. No rate, date or code is spelled in a component; what a screen names is
 * what the server's pack names, drawn in Italian through the tables below.
 */

export type TaxCalendarEntry = components['schemas']['TaxCalendarEntry']
export type TaxCalendar = components['schemas']['TaxCalendar']
export type TaxReadingLine = components['schemas']['TaxReadingLine']
export type TaxReadings = components['schemas']['TaxReadings']
export type DeclaredFigure = components['schemas']['DeclaredTaxFigureRead']
export type FiscalEstimate = components['schemas']['FiscalEstimate']
export type OrdinaryEstimate = components['schemas']['OrdinaryEstimate']
export type OrdinaryTax = components['schemas']['OrdinaryTax']
export type OrdinaryLine = components['schemas']['OrdinaryLine']

export type Tax = TaxReadingLine['tax']
export type Stage = 'estimate' | 'declared' | 'paid'

/** The three readings of a tax, in the order of the columns. */
export const STAGES: readonly Stage[] = ['estimate', 'declared', 'paid']

/** «Stima», «Dichiarato», «Pagato»: the same three words on every screen, and an estimate is
 *  never drawn under the second (brief, «What a component can violate»). */
export const STAGE_LABELS: Record<Stage, string> = {
  estimate: 'Stima',
  declared: 'Dichiarato',
  paid: 'Pagato',
}

export const TAX_LABELS: Record<Tax, string> = {
  irpef: 'IRPEF',
  substitute_tax: 'Imposta sostitutiva',
  ires: 'IRES',
  irap: 'IRAP',
  inps: 'Contributi INPS',
}

/** The pack as a person names the regime, to stand beside the pack's id and version: the
 *  surface never says «pacchetto» without the regime next to it. */
export const REGIME_LABELS: Record<string, string> = {
  'it-flat-rate': 'regime forfettario',
  'it-standard': 'regime ordinario, persona fisica',
  'it-company': 'società di capitali',
  'non-it': 'azienda estera',
}

/** The pack that has no taxes to compute: the page says so in one sentence. */
export const FOREIGN_PACK = 'non-it'
/** The pack whose space reads «Tasse» as one light page with no tabs (brief, question 4). */
export const FLAT_RATE_PACK = 'it-flat-rate'

export const NOT_ONE_YET = '—'

export const LOADING_SENTENCE = 'Leggo le tasse…'
export const EMPTY_SENTENCE =
  'Qui compaiono le imposte e i contributi dell’anno, con stima, dichiarato e pagato.'
export const NO_PROFILE_SENTENCE =
  'Profilo fiscale non configurato: senza il coefficiente di redditività e le due aliquote non c’è niente da stimare.'
export const FOREIGN_SENTENCE =
  'Per questo regime fiscale non si calcolano imposte: qui non c’è niente da mostrare.'
export const FROZEN_SENTENCE =
  'Cifre dell’anno com’erano: il pacchetto attuale non le calcola più.'
export const LEGEND = 'In grassetto la cifra che vale: pagato, poi dichiarato, poi stima.'
export const MISSING_PARAMETER_SENTENCE =
  'Alcune righe sono «non calcolabile» perché manca un parametro: imposta i parametri fiscali in Impostazioni.'
const PARAMETER_LABELS: Record<string, string> = {
  profitability_coefficient: 'coefficiente di redditività',
  substitute_tax_rate: 'aliquota imposta sostitutiva',
  social_security_rate: 'aliquota INPS',
  inps_category: 'cassa INPS',
  inps_trade: 'gestione INPS (artigiani o commercianti)',
  inps_covered_elsewhere: 'se sei assicurato altrove o pensionato',
  inps_before_1996: 'se hai contributi versati prima del 1996',
}

/** What the estimate says is missing, by the name the settings form gives it; the generic
 *  sentence when the server names none. */
export function missingParametersSentence(names: readonly string[] | undefined): string {
  const labels = (names ?? []).map((name) => PARAMETER_LABELS[name] ?? name)
  if (labels.length === 0) return MISSING_PARAMETER_SENTENCE
  return `Alcune righe sono «non calcolabile» perché manca: ${labels.join(', ')}. Compilali nei parametri fiscali, in Impostazioni.`
}
export const NO_CALENDAR_SENTENCE = (year: number) =>
  `Il pacchetto fiscale di questo regime non porta ancora le date del ${year}: qui non c’è niente da mostrare. Non vuol dire che non ci sia niente da pagare.`
export const NOTHING_DUE = 'Nessuna scadenza fiscale in vista.'
export const SALVO_PROROGHE = 'Le date sono quelle del pacchetto fiscale, salvo proroghe.'

/** «Pacchetto fiscale it-company, versione 1, legge in vigore dal 2026 (società di capitali)». */
export function packLine(estimate: Pick<FiscalEstimate, 'pack_id' | 'pack_version' | 'law_from_year'>): string | null {
  if (estimate.pack_id == null) return null
  const regime = REGIME_LABELS[estimate.pack_id]
  const law =
    estimate.law_from_year == null ? '' : `, legge in vigore dal ${estimate.law_from_year}`
  return `Pacchetto fiscale ${estimate.pack_id}, versione ${estimate.pack_version ?? '?'}${law}${regime ? ` (${regime})` : ''}`
}

export type AdvanceMethodName = 'historical' | 'forecast'

/** The two ways to compute a year's advances, as the server names them (`historical` is the
 *  default), each with the sentence under it. Which figure each one reads is the server's: the
 *  words only say it. */
export const ADVANCE_METHOD_LABELS: Record<AdvanceMethodName, { label: string; hint: string }> = {
  historical: {
    label: 'Storico',
    hint: 'Sull’imposta dichiarata dell’anno prima, o sulla sua stima se non c’è un dichiarato.',
  },
  forecast: {
    label: 'Previsionale',
    hint: 'Sulla stima dell’anno in corso.',
  },
}

/** The income taxes a June payment can be split for (the server refuses any other). */
export const INCOME_TAXES: readonly Tax[] = ['irpef', 'substitute_tax', 'ires', 'irap']

export const PLANS_EMPTY_SENTENCE =
  'Qui compaiono i piani di rate: quelli che scegli per il saldo e il primo acconto di giugno e quelli di un avviso dell’Agenzia delle Entrate.'
export const NOT_WRITTEN_SENTENCE = (count: number) =>
  count === 1
    ? 'Un pagamento non è stato scritto: manca una cifra o un parametro.'
    : `${count} pagamenti non sono stati scritti: manca una cifra o un parametro.`
