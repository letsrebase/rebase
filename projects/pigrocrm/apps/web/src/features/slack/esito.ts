import { SLACK_OAUTH_START } from './queries'

/**
 * The four outcome codes `GET /api/slack/oauth/callback` redirects with, rendered in
 * Italian here: the Slack twin of `features/drive/esito.ts`, for the same reasons. Never
 * Slack's own `error`, never a `Conflict`'s problem document, and never the raw `?esito=`
 * value echoed back: a lookup in a fixed table, so a crafted link cannot put a sentence of
 * its own on the page.
 */
export const ESITO_MESSAGES: Record<string, string> = {
  collegato: 'Slack collegato.',
  negato: 'Autorizzazione negata: Slack non è stato collegato.',
  // Says nothing about the cause: the panel below re-reads the workspace and shows the true
  // state, which is more actionable than any sentence this table could carry.
  errore:
    'Il collegamento non è andato a buon fine. Controlla qui sotto lo stato di Slack e riprova.',
  sessione: 'La sessione è scaduta mentre eri su Slack, quindi Slack non è stato collegato.',
}

export function messaggioEsito(esito: string | undefined): string | null {
  if (esito === undefined) return null
  // An own property only: `?esito=__proto__` must read as no outcome, not as an object.
  return Object.hasOwn(ESITO_MESSAGES, esito) ? (ESITO_MESSAGES[esito] ?? null) : null
}

/** Where «Riprova» under the outcome leads, or null for an outcome that offers none. */
export function riprovaEsito(esito: string | undefined): string | null {
  return esito === 'sessione' ? SLACK_OAUTH_START : null
}
