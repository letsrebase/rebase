import { useState } from 'react'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import { Label } from '@rebase/ui/label'
import { toProblem } from '@/lib/api'
import { tenantPrefix } from '@/lib/tenant'
import { formatInstant } from './instants'
import {
  GMAIL_READONLY_SCOPE,
  useDisconnectGmail,
  useGmailHealth,
  useSetStoreBodies,
  useSyncGmail,
  type GmailHealth,
  type GoogleAccountRead,
  type SyncReport,
} from './queries'

// Under a space, and under the root's own name, the API answers at `/<slug>/api/...` and
// the session cookie is scoped to that prefix: a plain anchor to `/api/...` reaches the
// root API with no cookie and answers «Autenticazione richiesta» (live, 2026-09-09).
const OAUTH_START = `${tenantPrefix}/api/gmail/oauth/start`

/** The four statuses, in the words their owner would use. */
const STATUS_LABEL: Record<string, string> = {
  active: 'attiva',
  expired: 'consenso scaduto',
  revoked: 'consenso revocato',
  disconnected: 'scollegata',
}

function reportText(report: SyncReport): string {
  if (report.already_running) {
    const since = report.running_since
    return since
      ? `Una sincronizzazione è già in corso da ${formatInstant(since)}.`
      : 'Una sincronizzazione è già in corso.'
  }
  return (
    `Sincronizzazione completata: ${report.messages_stored} email archiviate, ` +
    `${report.links_created} collegamenti creati.`
  )
}

/**
 * Impostazioni → Gmail.
 *
 * Four screens behind one query, and the order they are decided in is the whole design:
 *
 * 1. **The read failed.** The banner, and nothing under it. This panel writes back
 *    through PATCH and DELETE, so a control rendered from a response that never arrived
 *    would offer to change a setting nobody managed to show the user -- the defect the
 *    emitter panel was fixed for, and worse here because one of the buttons deletes
 *    somebody's correspondence.
 * 2. **`configured: false`.** This installation deliberately has no Google. An
 *    explanation and no button: a «Collega» here would answer 409.
 * 3. **Configured, no mailbox.** One button away from working. Telling this person
 *    "Gmail non è configurato" would send them looking for an environment variable that
 *    is already set -- which is why `GmailHealth` carries `configured` at all.
 * 4. **Connected.** State, scopes, the body-store switch, sync, re-authorise,
 *    disconnect.
 *
 * Every failure of an *action* is rendered from that mutation's own `error`, never
 * copied into component state: react-query clears it on the next attempt, so a message
 * cannot outlive the attempt that produced it. Both halves of that are asserted.
 */
export function GmailPanel() {
  const health = useGmailHealth()
  const sync = useSyncGmail()
  const disconnect = useDisconnectGmail()
  const storeBodies = useSetStoreBodies()
  const [eliminaMessaggi, setEliminaMessaggi] = useState(false)

  // The failure branch comes first, and it is not folded into the line below. On an
  // error `isPending` is false while `data` is still undefined, so a single
  // `isPending || !data` guard answers a failed read with «Caricamento…» — a spinner
  // that never resolves, and an error the user is never told about. Everything after
  // this line renders controls that write back through PATCH and DELETE; offering them
  // on top of a health response that never arrived is the defect the emitter and
  // fiscal panels were already fixed for.
  if (health.isError) return <QueryErrorBanner error={health.error} />
  if (health.isPending || !health.data) return <p className="text-muted-foreground">Caricamento…</p>

  const data: GmailHealth = health.data
  if (!data.configured) return <NotConfigured />

  const account = data.account
  if (account === null || account.status === 'disconnected') {
    return <NotConnected account={account} />
  }

  const canSync = account.status === 'active' && !data.missing_scopes.includes(GMAIL_READONLY_SCOPE)

  return (
    <section className="max-w-3xl space-y-6">
      <header className="space-y-1">
        <h2 className="text-lg font-medium">Casella collegata</h2>
        <p className="font-medium">{account.email_address}</p>
        <p className="text-sm text-muted-foreground">
          Stato: {STATUS_LABEL[account.status] ?? account.status} · Ultimo sync:{' '}
          {formatInstant(account.last_sync_at)}
          {account.consent_expires_at
            ? ` · Consenso da rinnovare entro il ${formatInstant(account.consent_expires_at)}`
            : ''}
        </p>
      </header>

      {data.banner_text ? (
        <p
          role="status"
          className="rounded-lg border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          {data.banner_text}
        </p>
      ) : null}

      {/* The recorded cause, next to the derived sentence rather than instead of it:
          `banner_text` says what to do, `last_error` says what happened and when. */}
      {account.last_error ? (
        <p className="text-sm text-muted-foreground">
          Ultimo errore: {account.last_error} ({formatInstant(account.last_error_at)})
        </p>
      ) : null}

      <div className="space-y-2">
        <h3 className="text-sm font-medium">Autorizzazioni concesse</h3>
        <ul className="flex flex-wrap gap-2">
          {account.scopes_granted.map((scope) => (
            <li key={scope}>
              <Badge variant="secondary" className="font-normal">
                {scope}
              </Badge>
            </li>
          ))}
        </ul>
        {data.missing_scopes.length > 0 ? (
          <p className="text-sm text-muted-foreground">
            Non concesse: {data.missing_scopes.join(', ')}
          </p>
        ) : null}
      </div>

      <div className="space-y-2">
        <div className="flex items-start gap-2">
          <Checkbox
            id="gmail-store-bodies"
            className="mt-1"
            checked={account.gmail_store_bodies}
            disabled={storeBodies.isPending}
            onCheckedChange={(checked) => storeBodies.mutate(checked === true)}
          />
          <Label htmlFor="gmail-store-bodies" className="font-normal leading-snug">
            Conserva il testo delle email nel database. Spegnendolo restano solo
            intestazioni e anteprima, e la corrispondenza già archiviata non si arricchisce
            più.
          </Label>
        </div>
        <ActionError error={storeBodies.error} />
      </div>

      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={() => sync.mutate()} disabled={!canSync || sync.isPending}>
            Sincronizza adesso
          </Button>
          <Button asChild variant="outline">
            <a href={OAUTH_START}>Ri-autorizza</a>
          </Button>
        </div>
        <ActionError error={sync.error} />
        {sync.data && !sync.isPending ? (
          <p role="status" className="text-sm text-muted-foreground">
            {reportText(sync.data)}
          </p>
        ) : null}
      </div>

      <div className="space-y-2 border-t pt-4">
        <div className="flex items-start gap-2">
          <Checkbox
            id="gmail-elimina-messaggi"
            className="mt-1"
            checked={eliminaMessaggi}
            onCheckedChange={(checked) => setEliminaMessaggi(checked === true)}
          />
          <Label htmlFor="gmail-elimina-messaggi" className="font-normal leading-snug">
            Elimina anche le email già archiviate nel CRM. È irreversibile.
          </Label>
        </div>
        <Button
          variant="destructive"
          disabled={disconnect.isPending}
          onClick={() => disconnect.mutate({ eliminaMessaggi })}
        >
          Scollega la casella
        </Button>
        <ActionError error={disconnect.error} />
      </div>
    </section>
  )
}

/** The server's own sentence for a failed action, or nothing. Derived from the
 *  mutation, so the next attempt clears it. */
function ActionError({ error }: { error: unknown }) {
  if (!error) return null
  return (
    <p className="text-sm text-destructive">{toProblem(error).detail}</p>
  )
}

function NotConfigured() {
  return (
    <section className="max-w-3xl space-y-2">
      <h2 className="text-lg font-medium">Gmail</h2>
      <p className="text-muted-foreground">
        Gmail non è configurato su questa installazione. Per attivarlo servono un client
        OAuth di Google e le variabili <code>PIGROCRM_GOOGLE_CLIENT_ID</code>,{' '}
        <code>PIGROCRM_GOOGLE_CLIENT_SECRET</code>, <code>PIGROCRM_GOOGLE_TOKEN_KEY</code> e{' '}
        <code>PIGROCRM_PUBLIC_URL</code>.
      </p>
    </section>
  )
}

/** Configured, but nothing connected -- either never, or unhooked on purpose. The
 *  second case still names the mailbox and the day: "it was disconnected" is a fact
 *  about the person's own action, and a screen that forgot it would read as data loss. */
function NotConnected({ account }: { account: GoogleAccountRead | null }) {
  return (
    <section className="max-w-3xl space-y-3">
      <h2 className="text-lg font-medium">Nessuna casella Google collegata</h2>
      {account ? (
        <p className="text-sm text-muted-foreground">
          La casella {account.email_address} è stata scollegata il{' '}
          {formatInstant(account.disconnected_at)}.
        </p>
      ) : null}
      <p className="text-muted-foreground">
        Collegando la tua casella, PigroCRM archivia solo le conversazioni con gli
        indirizzi già presenti in anagrafica.
      </p>
      <Button asChild>
        <a href={OAUTH_START}>Collega la casella Google</a>
      </Button>
    </section>
  )
}
