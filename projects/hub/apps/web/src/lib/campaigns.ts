import type { CampaignAzione, CampaignMeta, CampaignStato, RecipientStato } from './api'

export const CAMPAIGN_STATE_LABELS: Record<CampaignStato, string> = {
  bozza: 'Bozza',
  programmata: 'Programmata',
  in_invio: 'In invio',
  inviata: 'Inviata',
  annullata: 'Annullata',
}

export const AZIONE_LABELS: Record<CampaignAzione, string> = {
  entrato: 'È entrato nell’area',
  cv: 'Ha caricato il CV',
  scheda_completa: 'Ha completato la scheda',
  profilo_creato: 'Ha creato il profilo',
  richiesta_aggiornata: 'Ha aggiornato la richiesta',
  pigro_cliente: 'Primo cliente in Pigro',
}

/** Phase 1 offers three: the Pigro button arrives with phase 3. */
export const META_LABELS: Partial<Record<CampaignMeta, string>> = {
  area: 'La sua area',
  wizard: 'Il wizard del profilo',
  richiesta: 'La richiesta dell’azienda',
}

export const RECIPIENT_STATE_LABELS: Record<RecipientStato, string> = {
  in_coda: 'In coda',
  inviata: 'Inviata',
  saltata: 'Saltata',
  fallita: 'Fallita',
}

/** The server's rule (`campaigns/render.py:personalise`), for the preview on screen. */
export function personalise(testo: string, nome: string | null): string {
  if (nome) return testo.replaceAll('{nome}', nome)
  return testo.replaceAll(' {nome}', '').replaceAll('{nome}', '')
}

/** Today and the current time in Europe/Rome, as the «Programma» inputs want them. */
export function romeToday(now = new Date()): { giorno: string; ora: string } {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Europe/Rome',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    })
      .formatToParts(now)
      .map((part) => [part.type, part.value]),
  )
  return { giorno: `${parts.year}-${parts.month}-${parts.day}`, ora: `${parts.hour}:${parts.minute}` }
}

const QUARTER_HOUR_MS = 15 * 60 * 1000

/** What «Programma» proposes when it is chosen: an hour from now, rounded up to the
 *  next quarter hour, as a Rome day and time. Rome's offset is whole hours, so the
 *  quarter hours are the same instants in UTC and in Rome. */
export function defaultSchedule(now = new Date()): { giorno: string; ora: string } {
  const inAnHour = now.getTime() + 60 * 60 * 1000
  return romeToday(new Date(Math.ceil(inAnHour / QUARTER_HOUR_MS) * QUARTER_HOUR_MS))
}

/** The server's column limits (`rebase_core/models.py`, `CAMPAIGN_*_MAX_LENGTH`), so
 *  a field stops where the API would refuse it instead of failing the save. */
export const CAMPAIGN_MAX_LENGTH = { nome: 120, oggetto: 200, testo: 5000, bottone_testo: 60 } as const
