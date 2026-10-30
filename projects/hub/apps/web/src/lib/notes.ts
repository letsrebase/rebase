import type { ActionSource, NoteSource } from '@/lib/api'

/** What a note's (or a trail entry's) source reads as: the door it came through. `null`
 *  is a note from before the source was tracked, which shows no badge. */
export const NOTE_SOURCE_LABELS: Record<NoteSource, string> = {
  web: 'dal sito',
  mcp: 'MCP',
  persona: 'dalla persona',
  sistema: 'dal sistema',
}

/** The doors a regeneration of the cards adds (REB-861): the command line. */
export const ACTION_SOURCE_LABELS: Record<ActionSource, string> = {
  ...NOTE_SOURCE_LABELS,
  cli: 'da riga di comando',
}

export function noteSourceLabel(fonte: ActionSource | null | undefined): string | null {
  return fonte ? ACTION_SOURCE_LABELS[fonte] : null
}

/** Why a regeneration did not write a card, in a few words: the failures the writer
 *  records (`cards.Failure`) and the reasons of a batch that did not apply (REB-861). */
export const REGENERATION_REASON_LABELS: Record<string, string> = {
  no_text: 'CV senza testo',
  refusal: 'rifiutata dal modello',
  max_tokens: 'risposta interrotta',
  shape: 'risposta non valida',
  identifying: 'cita la persona o un indirizzo',
  source_identifying: 'cita un account, un sito o un datore di lavoro',
  gendered: 'rivela il genere',
  unavailable: 'modello non disponibile',
  cap_reached: 'tetto giornaliero raggiunto',
  not_configured: 'compito spento',
  superseded: 'CV o dati cambiati nel frattempo',
  errored: 'nessuna risposta dal Batch API',
  expired: 'batch scaduto',
  not_sent: 'batch non inviato',
}

export function regenerationReason(reason: string | undefined): string {
  return reason ? (REGENERATION_REASON_LABELS[reason] ?? reason) : 'motivo non registrato'
}
