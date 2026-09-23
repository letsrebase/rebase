/**
 * The start page's first door, «Collega Gmail» (spec 2026-09-16 §4.2 and §5, REB-222).
 *
 * Its state is `useGmailHealth`, the same read as Impostazioni → Gmail and the shell's
 * banner, so the door and the settings page cannot disagree. Four answers:
 *
 * - **No Google client for this space** (`configured: false`). Every space starts here:
 *   a space does not inherit the platform's client (`space_base_settings`), and whether
 *   it should is the open decision behind REB-223. So the door says what is missing and
 *   where it is set (Impostazioni → Spazio), and offers no button that would answer 409.
 * - **Nothing connected**, or a mailbox unhooked on purpose: the anchor that starts the
 *   consent flow, a navigation and never a fetch. The callback comes back to this page
 *   while the space is empty (`routers/gmail.py::_back`).
 * - **Connected, consent lapsed** (`expired`, `revoked`): the server's own sentence and
 *   the same anchor to give it again.
 * - **Connected**: «Casella collegata: i tuoi clienti arrivano tra poco», the line the
 *   spec keeps until REB-223's proposals replace it.
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
import { messaggioEsito } from '@/features/gmail/esito'
import { GMAIL_OAUTH_START, useGmailHealth } from '@/features/gmail/queries'

export function GmailDoor({ esito }: { esito?: string }) {
  const messaggio = messaggioEsito(esito)
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
      {messaggio ? (
        <p role="status" className="bg-muted/50 border px-3 py-2 text-sm">
          {messaggio}
        </p>
      ) : null}
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
      <div className="space-y-1 text-sm">
        <p className="font-medium">Casella collegata: i tuoi clienti arrivano tra poco</p>
        <p className="text-muted-foreground">{account.email_address}</p>
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
