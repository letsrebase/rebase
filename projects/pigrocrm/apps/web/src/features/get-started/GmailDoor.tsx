/**
 * The start page's first door, «Collega Gmail» (spec 2026-09-16 §4.2 and §5, REB-222).
 *
 * Its state is `useGmailHealth`, the same read as Impostazioni → Gmail and the shell's
 * banner, so the door and the settings page cannot disagree. Four answers:
 *
 * - **No Google client for this space** (`configured: false`). A space inherits the
 *   platform's client only where the platform lends it (`PIGROCRM_GOOGLE_SHARED_CLIENT`,
 *   `space_base_settings`, REB-394); without it the door says what is missing and where
 *   it is set (Impostazioni → Spazio), and offers no button that would answer 409. With
 *   it every space is configured, and the consent comes back here through the root's
 *   callback, which hands it to this space's own.
 * - **Nothing connected**, or a mailbox unhooked on purpose: the anchor that starts the
 *   consent flow, a navigation and never a fetch. The callback comes back to this page
 *   while the space is empty (`routers/gmail.py::_back`).
 * - **Connected, consent lapsed** (`expired`, `revoked`): the server's own sentence and
 *   the same anchor to give it again.
 * - **Connected**: the mailbox, and under it the customers it proposes, to tick and
 *   import (`SuggestedCustomers`, REB-223).
 *
 * Connecting is `require_write` in the service, and a mailbox is its owner's own
 * (`account_for_user`), so a readonly person reads why there is no button rather than
 * who could press it for them. `esito` is the callback's outcome, looked up in the
 * settings page's own table and never rendered as given.
 */
import { Link } from '@tanstack/react-router'
import { Mail } from 'lucide-react'
import { Button } from '@rebase/ui/button'
import { useAuth, useCanWrite } from '@/lib/auth'
import { ConsentOutcome } from '@/components/ConsentOutcome'
import { messaggioEsito, riprovaEsito } from '@/features/gmail/esito'
import { GMAIL_OAUTH_START, useGmailHealth } from '@/features/gmail/queries'
import { SuggestedCustomers } from '@/features/gmail/SuggestedCustomers'

export function GmailDoor({ esito }: { esito?: string }) {
  return (
    <section aria-labelledby="porta-gmail" className="space-y-3 border p-4">
      <header className="flex items-start gap-3">
        <Mail className="mt-0.5 size-5 shrink-0" aria-hidden />
        <div className="space-y-1">
          <h3 id="porta-gmail" className="font-medium">
            Collega Gmail
          </h3>
          <p className="text-muted-foreground text-sm">
            La casella da cui scrivi ai clienti: le conversazioni con chi è in anagrafica
            finiscono sulla sua scheda.
          </p>
        </div>
      </header>
      <ConsentOutcome messaggio={messaggioEsito(esito)} riprova={riprovaEsito(esito)} />
      <DoorState />
    </section>
  )
}

function DoorState() {
  const health = useGmailHealth()
  const { user } = useAuth()
  const canWrite = useCanWrite()
  const isAdmin = user?.ruolo === 'admin'

  if (health.isError) {
    return (
      <p className="text-muted-foreground text-sm">
        Non riesco a leggere lo stato della casella: riprova tra poco.
      </p>
    )
  }
  if (health.isPending) return <p className="text-muted-foreground text-sm">Caricamento…</p>

  const data = health.data
  if (!data.configured) {
    return isAdmin ? (
      <p className="text-sm">
        Per collegare Gmail a questo spazio serve un client OAuth di Google, da impostare
        in{' '}
        <Link to="/app/settings/space" className="font-medium underline underline-offset-4">
          Impostazioni → Spazio
        </Link>
        .
      </p>
    ) : (
      <p className="text-muted-foreground text-sm">
        Per collegare Gmail a questo spazio serve un client OAuth di Google: lo imposta
        l’amministratore dello spazio, in Impostazioni → Spazio.
      </p>
    )
  }

  const account = data.account
  if (account && account.status === 'active') {
    return (
      <div className="space-y-3">
        <div className="space-y-1 text-sm">
          <p className="font-medium">Casella collegata</p>
          <p className="text-muted-foreground">{account.email_address}</p>
        </div>
        {canWrite ? <SuggestedCustomers /> : null}
      </div>
    )
  }

  const lapsed = account !== null && account.status !== 'disconnected'
  return (
    <div className="space-y-2 text-sm">
      {lapsed && data.banner_text ? <p role="status">{data.banner_text}</p> : null}
      {canWrite ? (
        <Button asChild>
          <a href={GMAIL_OAUTH_START}>{lapsed ? 'Ricollega Gmail' : 'Collega Gmail'}</a>
        </Button>
      ) : (
        <p className="text-muted-foreground">
          Con un accesso in sola lettura non si collega una casella.
        </p>
      )}
    </div>
  )
}
