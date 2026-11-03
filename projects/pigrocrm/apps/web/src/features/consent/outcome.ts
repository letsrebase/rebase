import { DRIVE_OAUTH_START } from '@/features/drive/queries'
import { GMAIL_OAUTH_START } from '@/features/gmail/queries'
import { SLACK_OAUTH_START } from '@/features/slack/queries'

/**
 * The four outcome codes `GET /api/{gmail,drive,slack}/oauth/callback` redirect with,
 * rendered in Italian here: the one table of consent-outcome sentences.
 *
 * The provider's own `error` is never forwarded: it is English and occasionally embeds the
 * client id. Nor is a `Conflict` from the token exchange -- an RFC 9457 document in the
 * address bar strands the user outside the SPA at the end of a consent flow. So the
 * callback ends on a page of the SPA with one of four codes, and this table is the only
 * place they become words. The consumers are the provider's settings page, the Home (its
 * dashboard, or the start page while the space is empty, whose Gmail door is where the
 * flow started, REB-222) and «Primi passi», where a consent that came back with no session
 * lands (REB-446).
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
export type ConsentProvider = 'gmail' | 'drive' | 'slack'

interface ProviderOutcomes {
  /** Where «Riprova» leads after a session that ran out. */
  start: string
  messages: Record<'collegato' | 'negato' | 'errore' | 'sessione', string>
}

const PROVIDERS: Record<ConsentProvider, ProviderOutcomes> = {
  gmail: {
    start: GMAIL_OAUTH_START,
    messages: {
      collegato: 'Casella Google collegata.',
      negato: 'Autorizzazione negata: la casella non è stata collegata.',
      // Deliberately says nothing about the cause. The panel below re-reads the account
      // and shows the true state -- which mailbox is connected, and the button that
      // disconnects it -- and that is more actionable than any sentence this table could
      // carry.
      errore:
        'Il collegamento non è andato a buon fine. Controlla qui sotto lo stato della ' +
        'casella e riprova.',
      // The browser came back from Google with no live session (REB-446): nothing was
      // redeemed, and the SPA's login has just brought the person to «Primi passi».
      // Followed by «Riprova», since starting again is the whole of what is left.
      sessione:
        'La sessione è scaduta mentre eri su Google, quindi la casella non è stata collegata.',
    },
  },
  drive: {
    start: DRIVE_OAUTH_START,
    messages: {
      collegato: 'Google Drive collegato.',
      negato: 'Autorizzazione negata: Drive non è stato collegato.',
      errore:
        'Il collegamento non è andato a buon fine. Controlla qui sotto lo stato di Drive e ' +
        'riprova.',
      sessione: 'La sessione è scaduta mentre eri su Google, quindi Drive non è stato collegato.',
    },
  },
  slack: {
    start: SLACK_OAUTH_START,
    messages: {
      collegato: 'Slack collegato.',
      negato: 'Autorizzazione negata: Slack non è stato collegato.',
      errore:
        'Il collegamento non è andato a buon fine. Controlla qui sotto lo stato di Slack e riprova.',
      sessione: 'La sessione è scaduta mentre eri su Slack, quindi Slack non è stato collegato.',
    },
  },
}

/**
 * What a consent callback's `?esito=` says, as the two props of `ConsentOutcome`:
 * `messaggio` is the sentence (null for no code or an unknown one) and `riprova` where
 * «Riprova» leads, null for an outcome that offers none.
 */
export function consentOutcome(
  provider: ConsentProvider,
  code: string | undefined,
): { messaggio: string | null; riprova: string | null } {
  const { start, messages } = PROVIDERS[provider]
  // An own property only: `?esito=__proto__` must read as no outcome, not as an object.
  const messaggio =
    code !== undefined && Object.hasOwn(messages, code)
      ? messages[code as keyof typeof messages]
      : null
  return { messaggio, riprova: code === 'sessione' ? start : null }
}
