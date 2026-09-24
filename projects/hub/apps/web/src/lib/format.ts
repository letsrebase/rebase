const euro = new Intl.NumberFormat('it-IT', { style: 'currency', currency: 'EUR', useGrouping: 'always' })
const day = new Intl.DateTimeFormat('it-IT', { day: 'numeric', month: 'short', year: 'numeric' })
const moment = new Intl.DateTimeFormat('it-IT', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
})

/** `"450.00"` from the API, as `450,00 €`. Parsed for display only; nothing here is summed. */
export function formatEuro(value: string): string {
  return euro.format(Number(value))
}

export function formatDate(value: string): string {
  return day.format(new Date(value))
}

/** Day and time, for things that happen several times a day: a comment thread. */
export function formatDateTime(value: string): string {
  return moment.format(new Date(value))
}

export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

export const REMOTO_LABELS = { remoto: 'Da remoto', ibrido: 'Ibrido', in_sede: 'In sede' } as const
export const FREELANCER_STATES = ['nuovo', 'contattato', 'attivo', 'scartato'] as const
/** The states the freelancer list filters by: the card's four, plus the pseudo-state of a
 *  signup with no card (ORB-163). */
export const FREELANCER_LIST_STATES = [...FREELANCER_STATES, 'lead'] as const
export const COMPANY_STATES = ['nuovo', 'contattato', 'in_corso', 'chiuso'] as const
export const STATE_LABELS: Record<string, string> = {
  nuovo: 'Nuovo',
  lead: 'Lead',
  contattato: 'Contattato',
  attivo: 'Attivo',
  scartato: 'Scartato',
  in_corso: 'In corso',
  chiuso: 'Chiuso',
}

/** REB-413: the state chips on the «Match» list, in the order the brief gives them. */
export const MATCH_STATES = ['bozza', 'in_firma', 'attivo', 'concluso', 'annullato'] as const
/** REB-387: what a match and a contract document are, in the admin's words. */
export const MATCH_STATE_LABELS: Record<string, string> = {
  bozza: 'Bozza',
  in_firma: 'In firma',
  attivo: 'Attivo',
  concluso: 'Concluso',
  annullato: 'Annullato',
}
export const DOCUMENT_STATE_LABELS: Record<string, string> = {
  generato: 'Generato',
  in_attesa: 'In attesa del contratto quadro',
  inviato: 'Inviato',
  firmato: 'Firmato',
  annullato: 'Annullato',
  disdetto: 'Disdetto',
}

/** REB-392: a contract's state in the freelancer's own words. */
export const MEMBER_DOCUMENT_STATE_LABELS: Record<string, string> = {
  in_attesa: 'Parte dopo la firma del contratto quadro',
  inviato: 'Da firmare',
  firmato: 'Firmato',
  annullato: 'Annullato: non va più firmato',
  disdetto: 'Disdetto',
}
