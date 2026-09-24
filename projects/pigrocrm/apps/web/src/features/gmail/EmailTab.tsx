import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { EmailThread } from './EmailThread'
import { PendingDrafts } from './PendingDrafts'
import { useGmailMessages, type GmailEntityType, type GmailMessageRead } from './queries'

/** The most recent instant in a thread. Computed rather than read off the last element:
 *  the API's ordering is `(gmail_thread_id, internal_date)`, which this code should not
 *  have to depend on to put the live conversation at the top. */
function lastActivity(thread: GmailMessageRead[]): number {
  return Math.max(...thread.map((message) => new Date(message.internal_date).getTime()))
}

function byThread(messages: GmailMessageRead[]): [string, GmailMessageRead[]][] {
  const grouped = new Map<string, GmailMessageRead[]>()
  for (const message of messages) {
    const bucket = grouped.get(message.gmail_thread_id) ?? []
    bucket.push(message)
    grouped.set(message.gmail_thread_id, bucket)
  }
  // Most recently active conversation first: that is the one being worked on. Within a
  // thread the order is left as the API returned it -- oldest first -- because a
  // conversation reads forwards.
  return [...grouped.entries()].sort((a, b) => lastActivity(b[1]) - lastActivity(a[1]))
}

/**
 * The Email tab on a customer, a person or a deal.
 *
 * Reads the stored mirror and nothing else: there is no parameter here that reaches
 * Gmail (spec 8.2), so this tab works with the mailbox disconnected and costs nothing
 * against anybody's quota. The same message is filed against the person, their customer
 * and that customer's live deals, so the three tabs are three questions to one endpoint
 * rather than three different features.
 *
 * Writing an email is the assistant's job (`draft_email` over MCP), and it never sends.
 * The tab was read-only from 2026-09-09 to 2026-09-24; since REB-415 it lists the
 * drafts that have not left above the correspondence (`PendingDrafts`), each one whole
 * and with the «Invia» a person presses after reading it. There is still no composer:
 * the drafts come from the assistant, the person reviews and sends.
 */
export function EmailTab({
  entityType,
  entityId,
}: {
  entityType: GmailEntityType
  entityId: string
}) {
  return (
    <div className="space-y-6">
      <PendingDrafts entityType={entityType} entityId={entityId} />
      <Correspondence entityType={entityType} entityId={entityId} />
    </div>
  )
}

/** What actually went back and forth. Its own component, so a failed read here and a
 *  failed read of the drafts above are two banners and neither hides the other. */
function Correspondence({
  entityType,
  entityId,
}: {
  entityType: GmailEntityType
  entityId: string
}) {
  const messages = useGmailMessages({ entityType, entityId })

  // The failure branch first. On an error `isPending` is false while `data` is still
  // undefined, so a `isPending || !data` guard would answer a failed request with
  // «Caricamento…» forever -- and the branch below it would be unreachable. An empty
  // tab and a request that never arrived are two different claims, and making the
  // second one look like the first is the defect `QueryErrorBanner` exists for.
  if (messages.isError) return <QueryErrorBanner error={messages.error} />
  if (messages.isPending || !messages.data)
    return <p className="text-muted-foreground">Caricamento…</p>

  return (
    <section className="space-y-6">
      <h3 className="text-sm font-medium text-muted-foreground">Corrispondenza</h3>

      {messages.data.length === 0 ? (
        <p className="text-muted-foreground">
          Nessuna email sincronizzata per questa scheda. Le conversazioni compaiono quando un
          indirizzo di questa scheda è presente in anagrafica e il sync è stato eseguito.
        </p>
      ) : (
        byThread(messages.data).map(([threadId, thread]) => (
          <EmailThread key={threadId} messages={thread} />
        ))
      )}
    </section>
  )
}
