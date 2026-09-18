import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { formatInstant } from './instants'
import type { GmailMessageRead } from './queries'

/**
 * `attachments` arrives typed as `Record<string, unknown>[]` -- the schema stores a
 * JSON column and openapi-typescript can say no more than that -- so the three keys
 * `parse.py` actually writes are read defensively here rather than cast. A row from an
 * older sync missing one of them renders a blank cell, never `undefined` on screen.
 */
function attachmentLabel(attachment: Record<string, unknown>): string {
  const filename = typeof attachment.filename === 'string' ? attachment.filename : ''
  const mime = typeof attachment.mime === 'string' ? attachment.mime : ''
  const size = typeof attachment.size === 'number' ? attachment.size : 0
  const parts = [filename || 'allegato', mime, `${Math.max(1, Math.round(size / 1024))} KB`]
  return parts.filter(Boolean).join(' · ')
}

/**
 * One Gmail thread, oldest message first, as the CRM stored it.
 *
 * Deliberately not a mail client. The stored mirror is plain text, headers and the
 * names of the attachments; for the original -- HTML, images, full headers, the reply
 * box -- there is Gmail, which every message deep-links into by its own id. Building a
 * second, worse Gmail inside a CRM is how this feature would grow without bound.
 */
export function EmailThread({ messages }: { messages: GmailMessageRead[] }) {
  const first = messages[0]
  if (!first) return null
  const subject = first.subject || '(senza oggetto)'

  return (
    <section role="group" aria-label={subject} className="space-y-3">
      <h3 className="text-base font-medium">{subject}</h3>
      {messages.map((message) => (
        <article key={message.id} className="space-y-2 rounded-lg border bg-card p-3">
          <header className="flex flex-wrap items-baseline gap-2 text-sm">
            {/* Direction is decided at sync time against the connected mailbox, not
                re-derived here from the roster -- which would call every message
                inbound, since the roster is precisely the people one writes to. */}
            <Badge variant={message.direction === 'inbound' ? 'secondary' : 'outline'}>
              {message.direction === 'inbound' ? 'Ricevuta' : 'Inviata'}
            </Badge>
            <span className="font-medium">{message.from_address}</span>
            <span className="text-muted-foreground">{formatInstant(message.internal_date)}</span>
            <a
              className="ml-auto underline underline-offset-2"
              href={`https://mail.google.com/mail/u/0/#all/${message.gmail_message_id}`}
              target="_blank"
              rel="noreferrer"
            >
              Apri in Gmail
            </a>
          </header>

          <p className="whitespace-pre-wrap text-sm">{message.body_text || message.snippet}</p>

          {/* The three honesty markers. A stored body that is not the whole story has
              to say so: showing a shortened, converted or never-archived message as if
              it were the original is the CRM believing something other than what
              happened -- and the reader has no way to notice on their own. */}
          {message.body_text ? null : (
            <p className="text-xs text-muted-foreground">
              Il testo completo non è archiviato per questa casella: questa è l&apos;anteprima
              di Gmail.
            </p>
          )}
          {message.body_truncated ? (
            <p className="text-xs text-muted-foreground">
              {/* No byte figure: the ceiling is `gmail_body_max_bytes`, configurable per
                  installation, so a number written here would be wrong on any install
                  that changed it. */}
              Corpo troncato: il messaggio superava il limite di archiviazione.
              L&apos;originale è in Gmail.
            </p>
          ) : null}
          {message.body_html_scartato ? (
            <p className="text-xs text-muted-foreground">
              Il messaggio era solo HTML: qui è conservata la conversione in testo. L&apos;HTML
              non viene mostrato perché porta con sé pixel di tracciamento e CSS remoto.
            </p>
          ) : null}

          {message.attachments.length > 0 ? (
            <ul className="space-y-1 text-sm">
              {message.attachments.map((attachment, index) => (
                <li
                  // The index is part of the key because nothing about an attachment is
                  // unique: two files with the same name and size on one message is
                  // unusual, not impossible, and Gmail assigns no id we store.
                  key={`${String(attachment.filename)}-${index}`}
                  className="flex flex-wrap items-center gap-2"
                >
                  <span>{attachmentLabel(attachment)}</span>
                  {/* Spec 5.4 stores no bytes, so saving one means fetching it from
                      Gmail and writing it through DocumentService -- an endpoint this
                      slice's REST surface (spec 8.3) does not have. Disabled and
                      labelled, rather than a button that 404s or a name silently
                      rendered as inert text. */}
                  <Button variant="ghost" size="sm" disabled title="In arrivo">
                    Salva come documento
                  </Button>
                </li>
              ))}
            </ul>
          ) : null}
        </article>
      ))}
    </section>
  )
}
