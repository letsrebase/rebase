import { GMAIL_OAUTH_START } from './queries'

/**
 * The four outcome codes `GET /api/gmail/oauth/callback` redirects with, rendered in
 * Italian here.
 *
 * Google's own `error` is never forwarded: it is English and occasionally embeds the
 * client id. Nor is a `Conflict` from the token exchange -- an RFC 9457 document in the
 * address bar strands the user outside the SPA at the end of a consent flow. So the
 * callback ends on a page of the SPA with one of four codes, and this table is the only
 * place they become words. Three pages read it, each through `ConsentOutcome`:
 * Impostazioni → Gmail, the Home (its dashboard, or the start page while the space is
 * empty, whose Gmail door is where the flow started, REB-222) and «Primi passi», where a
 * consent that came back with no session lands (REB-446).
 *
 * A lookup in a fixed table rather than rendering the parameter: `?esito=` arrives from
 * the address bar, and echoing it would let anybody who can get a link clicked put a
 * sentence of their choosing on the page. An unknown code renders nothing at all.
 *
 * A plain module, not a component file: a component module that also exported this
 * table would trip `react-refresh/only-export-components`, and the route file that
 * consumes it must export nothing but `Route` or it opts that route out of the router
 * plugin's code splitting.
 */
export const ESITO_MESSAGES: Record<string, string> = {
  collegato: 'Casella Google collegata.',
  negato: 'Autorizzazione negata: la casella non è stata collegata.',
  // Deliberately says nothing about the cause. The panel below re-reads the account and
  // shows the true state -- which mailbox is connected, and the button that disconnects
  // it -- and that is more actionable than any sentence this table could carry.
  errore:
    'Il collegamento non è andato a buon fine. Controlla qui sotto lo stato della ' +
    'casella e riprova.',
  // The browser came back from Google with no live session (REB-446): nothing was
  // redeemed, and the SPA's login has just brought the person to «Primi passi». Followed
  // by «Riprova» (`riprovaEsito`), since starting again is the whole of what is left.
  sessione:
    'La sessione è scaduta mentre eri su Google, quindi la casella non è stata collegata.',
}

export function messaggioEsito(esito: string | undefined): string | null {
  if (esito === undefined) return null
  return ESITO_MESSAGES[esito] ?? null
}

/** Where «Riprova» under the outcome leads, or null for an outcome that offers none. */
export function riprovaEsito(esito: string | undefined): string | null {
  return esito === 'sessione' ? GMAIL_OAUTH_START : null
}
