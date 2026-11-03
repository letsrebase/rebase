/**
 * A deal's economic state as the P&L and the time summary say it: `in_progress`, `to_invoice`
 * and `closed` are the API's codes, the words are what a person reads (they were the codes
 * themselves, in Italian, before the English rename of 2026-11-02).
 */
export const DEAL_STATUS_LABELS: Record<string, string> = {
  in_progress: 'in corso',
  to_invoice: 'da fatturare',
  closed: 'chiuso',
}

export function dealStatusLabel(status: string): string {
  return Object.hasOwn(DEAL_STATUS_LABELS, status) ? DEAL_STATUS_LABELS[status]! : status
}
