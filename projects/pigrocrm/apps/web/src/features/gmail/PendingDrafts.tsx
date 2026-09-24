import { Paperclip, Search, Send, Trash2 } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useRef, useState } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { toast } from '@rebase/ui/sonner'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { toProblem } from '@/lib/api'
import { useCan } from '@/lib/auth'
import {
  isSendable,
  patchCachedDraft,
  useDeleteDraft,
  useDraftsForEntity,
  useReconcileDraft,
  useSendDraft,
  type EmailDraftAttachment,
  type EmailDraftRead,
  type SendState,
} from './draftQueries'
import {
  SEND_OUTCOME_UNKNOWN,
  STATE_HEADING,
  STATE_HELP,
  draftCannotLeave,
  failureMovedTheDraft,
  mailboxCannotSend,
  outcomeSentence,
  promisesAnAttachmentItDoesNotHave,
  sendOutcomeUnknown,
} from './draftStates'
import { formatInstant } from './instants'
import { useGmailHealth, type GmailEntityType } from './queries'

function subjectOf(draft: EmailDraftRead): string {
  return draft.subject === '' ? '(senza oggetto)' : draft.subject
}

function attachmentLabel(attachment: EmailDraftAttachment): string {
  // `null` is a version the send would refuse (`EmailDraftAttachment` in the core schema):
  // gone, or of a type it does not attach. Said, never dropped: a draft that silently lost
  // an attachment on screen would be refused at the press for a file the person cannot
  // see. «Invia» is off for it (`draftCannotLeave`).
  if (attachment.filename === null)
    return 'Allegato non inviabile: il file non esiste più o il suo tipo non è ammesso'
  const kb = Math.max(1, Math.round((attachment.dimensione ?? 0) / 1024))
  return `${attachment.filename} · ${kb} KB`
}

/**
 * The drafts filed against one customer, person or deal that have not left yet, each one
 * shown whole, with the press that sends it (REB-415).
 *
 * The drafts come from the assistant (`draft_email` over MCP, which cannot send) and the
 * person reviews and sends: this is the other half of that tool, deliberately a person.
 * So every draft is shown exactly as it will leave -- from, to, cc, subject, the body as
 * plain text (it is sent as `text/plain`, verbatim) and the attachments by the file name
 * the recipient will see -- and there is no composer: nothing here edits the text.
 *
 * They sit *above* the correspondence, not inside it. An unsent draft is not part of the
 * conversation the client has seen, and putting it in the thread would show a message
 * that never left -- «il CRM crede una cosa diversa da quella che è successa» -- in the one
 * place a person looks to find out what was said.
 *
 * A failed read renders its own banner and nothing else: the correspondence below is a
 * second read and a second claim, and it must not go down with this one.
 */
export function PendingDrafts({
  entityType,
  entityId,
}: {
  entityType: GmailEntityType
  entityId: string
}) {
  const drafts = useDraftsForEntity({ entityType, entityId })
  const headingId = useId()

  if (drafts.isError) return <QueryErrorBanner error={drafts.error} />
  // Nothing while pending, rather than «Caricamento…»: the correspondence below has its
  // own, and two spinners for one tab read as two different things going wrong.
  const pending = (drafts.data?.items ?? []).filter((draft) => draft.send_state !== 'inviato')
  if (pending.length === 0) return null

  return (
    <section aria-labelledby={headingId} className="space-y-3">
      <div className="space-y-1">
        <h3 id={headingId} className="text-sm font-medium text-muted-foreground">
          Bozze da inviare
        </h3>
        <p className="text-sm text-muted-foreground">
          Non sono ancora partite. Leggile prima di inviarle: parte esattamente quello che vedi.
        </p>
      </div>
      {pending.map((draft) => (
        <DraftCard key={draft.id} draft={draft} />
      ))}
    </section>
  )
}

function DraftCard({ draft }: { draft: EmailDraftRead }) {
  // REB-294: one check per button, each against the string its own service passes to
  // `require_write` (`gmail/send.py`, `gmail/drafts.py`). A readonly person reads the
  // draft and is offered nothing to press.
  const maySend = useCan("inviare un'email")
  const mayVerify = useCan("verificare l'esito di un'email")
  const mayDelete = useCan('delete_email_draft')
  const queryClient = useQueryClient()
  const health = useGmailHealth()
  const send = useSendDraft()
  const reconcile = useReconcileDraft()
  const remove = useDeleteDraft()
  const [confirming, setConfirming] = useState(false)
  const attachmentsId = useId()
  // Synchronous, unlike `isPending`: two clicks dispatched before React re-renders both
  // read a `disabled` that is still false, and a flag read from the closure would be too.
  // This is the guard that holds; `disabled` is the one the person sees. Cleared when the
  // send stops being pending rather than from a per-call callback, which a `reset()` or
  // an unmount would drop and leave «Invia ora» dead for good.
  const sendingOnce = useRef(false)
  useEffect(() => {
    if (!send.isPending) sendingOnce.current = false
  }, [send.isPending])

  const state = draft.send_state
  const sendable = isSendable(state)
  // `in_invio` too: a send whose request died after the claim leaves the draft there,
  // and `reconcile` resolves it exactly as it resolves `incerto` (and answers it
  // unchanged while it may still be in flight).
  const verifiable = state === 'incerto' || state === 'in_invio'
  const busy = send.isPending || reconcile.isPending || remove.isPending
  const from = health.data?.account?.email_address ?? null
  const blocked = draftCannotLeave(draft.attachments) ?? mailboxCannotSend(health.data)
  const missingAttachment = promisesAnAttachmentItDoesNotHave({
    body: draft.body_markdown,
    attachmentCount: draft.attachments.length,
  })
  const subject = subjectOf(draft)

  function onSend() {
    if (sendingOnce.current) return
    sendingOnce.current = true
    reconcile.reset()
    remove.reset()
    send.mutate(draft.id, {
      onSuccess: (sent) => {
        setConfirming(false)
        if (sent.send_state === 'inviato')
          toast.success('Email inviata: la trovi nella corrispondenza.')
        patchCachedDraft(queryClient, draft, sent)
      },
      // Closed on a failure too: the outcome is on the card now (its new state, or the
      // banner), and a dialog still asking «Inviare questa email?» over a refusal would
      // read as though nothing had happened.
      onError: (error) => {
        setConfirming(false)
        const moved = toProblem(error).send_state
        if (typeof moved === 'string')
          patchCachedDraft(queryClient, draft, { send_state: moved as SendState })
      },
    })
  }

  function onVerify() {
    send.reset()
    remove.reset()
    reconcile.mutate(draft.id, {
      onSuccess: (checked) => {
        if (checked.send_state === 'inviato') {
          toast.success('L’email risulta partita: la trovi nella corrispondenza.')
        } else if (checked.send_state === state) {
          // `reconcile` answers the draft unchanged inside the grace window: Gmail's
          // search index is not instantaneous, and «non ancora» is a true answer where
          // «non è partita» would not be.
          toast.message('Gmail non la mostra ancora. Riprova tra qualche minuto, senza rinviarla.')
        }
        // `fallito` needs no toast: the card now carries the server's own sentence,
        // which names «Posta inviata» before offering «Invia» again.
        patchCachedDraft(queryClient, draft, checked)
      },
    })
  }

  function onDelete() {
    if (!window.confirm('Eliminare questa bozza? Il testo non si può recuperare.')) return
    send.reset()
    reconcile.reset()
    remove.mutate(draft.id, { onSuccess: () => toast.success('Bozza eliminata') })
  }

  return (
    <article aria-label={subject} className="space-y-3 border bg-card p-3">
      <header className="flex flex-wrap items-baseline gap-2 text-sm">
        <Badge variant={state === 'fallito' || state === 'incerto' ? 'destructive' : 'outline'}>
          {STATE_HEADING[state]}
        </Badge>
        <span className="text-muted-foreground">preparata {formatInstant(draft.created_at)}</span>
      </header>

      <Envelope draft={draft} from={from} subject={subject} />

      {/* Plain text on purpose: the send puts `body_markdown` into a `text/plain` part
          verbatim (`rfc822.build_rfc822`), so rendering it as Markdown would show the
          person a formatted message their client will never see. */}
      <figure aria-label="Testo" className="border-l-2 pl-3">
        <p className="whitespace-pre-wrap text-sm wrap-anywhere">{draft.body_markdown}</p>
      </figure>

      <div className="space-y-1 text-sm">
        <p id={attachmentsId} className="text-muted-foreground">
          Allegati
        </p>
        {draft.attachments.length === 0 ? (
          <p>Nessun allegato.</p>
        ) : (
          <ul aria-labelledby={attachmentsId} className="space-y-1">
            {draft.attachments.map((attachment) => (
              <li key={attachment.version_id} className="flex items-center gap-2 wrap-anywhere">
                <Paperclip className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
                {attachmentLabel(attachment)}
              </li>
            ))}
          </ul>
        )}
      </div>

      {missingAttachment ? (
        <p
          role="status"
          className="border border-amber-500/50 bg-amber-500/10 px-3 py-2 text-sm"
        >
          Il testo parla di un allegato, ma la bozza non ne ha nessuno: chi la riceve lo
          cercherà.
        </p>
      ) : null}

      {/* The server's own sentence about the last attempt, read off the row, so it goes
          away when the fact does rather than when a component remembers to reset it. Not
          for `incerto`, whose stored sentence is always the unanswered send's and says
          what `STATE_HELP.incerto` below already says. */}
      {draft.last_error && state !== 'incerto' ? (
        <p role="status" className="border bg-muted px-3 py-2 text-sm">
          {draft.last_error}
        </p>
      ) : null}

      <p className="text-xs text-muted-foreground">{STATE_HELP[state]}</p>

      {send.isError && !failureMovedTheDraft(send.error) ? (
        <p role="alert" className={ALERT}>
          {sendOutcomeUnknown(send.error) ? SEND_OUTCOME_UNKNOWN : outcomeSentence(send.error)}
        </p>
      ) : null}
      {reconcile.isError ? (
        <p role="alert" className={ALERT}>
          {outcomeSentence(reconcile.error)}
        </p>
      ) : null}
      {remove.isError ? (
        <p role="alert" className={ALERT}>
          {outcomeSentence(remove.error)}
        </p>
      ) : null}

      {sendable && maySend && blocked ? (
        <p className="text-sm text-muted-foreground">{blocked}</p>
      ) : null}

      <footer className="flex flex-wrap gap-2">
        {/* No «Invia» while the outcome is unknown, and no "riprova" either: `isSendable`
            is false for `incerto` and `in_invio`, so this condition is the whole rule.
            The only way out of «esito da verificare» is to ask. */}
        {sendable && maySend ? (
          <Button
            type="button"
            disabled={blocked !== null || busy}
            onClick={() => {
              send.reset()
              setConfirming(true)
            }}
          >
            <Send data-icon="inline-start" />
            Invia
          </Button>
        ) : null}
        {verifiable && mayVerify ? (
          <Button type="button" onClick={onVerify} disabled={busy}>
            <Search data-icon="inline-start" />
            Verifica
          </Button>
        ) : null}
        {sendable && mayDelete ? (
          <Button type="button" variant="ghost" onClick={onDelete} disabled={busy}>
            <Trash2 data-icon="inline-start" />
            Elimina
          </Button>
        ) : null}
      </footer>

      {/* Held open while the send is in flight: closing it then would put «Elimina» and
          «Invia» back under the person's hand before the outcome is known. */}
      <Dialog
        open={confirming}
        onOpenChange={(open) => {
          if (!send.isPending) setConfirming(open)
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Inviare questa email?</DialogTitle>
            <DialogDescription>
              {/* The one irreversible press in the product: an email cannot be recalled,
                  and the client reads it as the sender's own words. */}
              Parte subito{from ? ` dalla tua casella ${from}` : ''} e non si può richiamare.
            </DialogDescription>
          </DialogHeader>
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
            <dt className="text-muted-foreground">A</dt>
            <dd className="wrap-anywhere">{draft.to_addresses.join(', ')}</dd>
            {draft.cc_addresses.length > 0 ? (
              <>
                <dt className="text-muted-foreground">Cc</dt>
                <dd className="wrap-anywhere">{draft.cc_addresses.join(', ')}</dd>
              </>
            ) : null}
            <dt className="text-muted-foreground">Oggetto</dt>
            <dd className="wrap-anywhere">{subject}</dd>
            <dt className="text-muted-foreground">Allegati</dt>
            <dd className="wrap-anywhere">
              {draft.attachments.length === 0
                ? 'nessuno'
                : draft.attachments.map((attachment) => attachment.filename).join(', ')}
            </dd>
          </dl>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={send.isPending}
              onClick={() => setConfirming(false)}
            >
              Annulla
            </Button>
            <Button type="button" onClick={onSend} disabled={send.isPending}>
              <Send data-icon="inline-start" />
              Invia ora
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </article>
  )
}

/** From, to, cc and subject, as the recipient's client will show them. */
function Envelope({
  draft,
  from,
  subject,
}: {
  draft: EmailDraftRead
  from: string | null
  subject: string
}) {
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
      {from ? (
        <>
          <dt className="text-muted-foreground">Da</dt>
          <dd className="wrap-anywhere">{from}</dd>
        </>
      ) : null}
      <dt className="text-muted-foreground">A</dt>
      <dd className="wrap-anywhere">{draft.to_addresses.join(', ')}</dd>
      {draft.cc_addresses.length > 0 ? (
        <>
          <dt className="text-muted-foreground">Cc</dt>
          <dd className="wrap-anywhere">{draft.cc_addresses.join(', ')}</dd>
        </>
      ) : null}
      <dt className="text-muted-foreground">Oggetto</dt>
      <dd className="font-medium wrap-anywhere">{subject}</dd>
    </dl>
  )
}

const ALERT = 'border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive'
