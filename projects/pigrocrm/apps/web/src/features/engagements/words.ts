import type { DaysReport, EngagementPayment } from './labels'

/** What a hidden client's amounts, role and fee read as (the brief: «Nascosto»), never a figure. */
export const HIDDEN = 'Nascosto'

/** The sentence under a date: «in attesa dell'incasso di Fattura 2026/71», as the server names the invoice. */
export function waitingWords(payment: EngagementPayment): string | null {
  return payment.waiting_for_invoice === null
    ? null
    : `in attesa dell'incasso di Fattura ${payment.waiting_for_invoice}`
}

/** «Giorni» or «Ore»: what the report counts for this letter. */
export function unitHeading(report: DaysReport | null | undefined): string {
  return report?.unit === 'hour' ? 'Ore' : 'Giorni'
}

/** «Trattieni 2 giorni e approva il resto»: the count is of the days ticked, the only arithmetic here. */
export function holdLabel(count: number): string {
  return `Trattieni ${count} ${count === 1 ? 'giorno' : 'giorni'} e approva il resto`
}
