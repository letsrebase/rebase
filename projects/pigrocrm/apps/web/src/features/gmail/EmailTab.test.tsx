import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { toast } from '@rebase/ui/sonner'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { EmailTab } from './EmailTab'
import type { EmailDraftRead } from './draftQueries'
import type { GmailMessageRead } from './queries'
import { api } from '@/lib/api'
import type { Role } from '@/lib/permissions'

// `api` is an openapi-fetch client built at import time, so it is mocked as a module
// (the shape GmailPanel.test.tsx established); stubbing `globalThis.fetch` would never
// be seen by it, and `vi.doMock` after this file's own static imports would be applied
// to nothing.
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), DELETE: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({
  toast: { success: vi.fn(), message: vi.fn() },
}))
// REB-415: each press on a draft asks `useCan` for its service's own string. The real
// table answers here, for the role the test sets, so a row that drifted from the core
// fails in this file as well as in `test/permissions.test.ts`.
const mockAuth = vi.hoisted(() => ({ role: 'admin' as Role }))
vi.mock('@/lib/auth', async () => {
  const { can } = await import('@/lib/permissions')
  return { useCan: (action: string) => can(mockAuth.role, action) }
})

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

function failed(error: unknown, status: number) {
  return { error, response: new Response(null, { status }) } as never
}

const ENTITY_ID = '00000000-0000-7000-8000-000000000001'

/** Deliberately anodyne fixtures. Nothing that looks like real correspondence goes
 *  into a test, because a failing assertion prints the rendered DOM. */
function message(overrides: Partial<GmailMessageRead> = {}): GmailMessageRead {
  return {
    id: 'a',
    gmail_message_id: 'm1',
    gmail_thread_id: 't1',
    direction: 'inbound',
    from_address: 'ada@acme.it',
    to_addresses: ['io@example.it'],
    cc_addresses: [],
    subject: 'Rinnovo',
    snippet: 'Anteprima',
    internal_date: '2026-08-18T10:00:00Z',
    body_text: 'Testo del messaggio.',
    body_truncated: false,
    body_html_scartato: false,
    attachments: [],
    ...overrides,
  }
}

const DRAFT_ID = '00000000-0000-7000-8000-0000000000d1'
const VERSION_ID = '00000000-0000-7000-8000-0000000000a1'
const SEND_SCOPE = 'https://www.googleapis.com/auth/gmail.send'

/** A draft as the assistant leaves it. Anodyne for the same reason as `message`. */
function draft(overrides: Partial<EmailDraftRead> = {}): EmailDraftRead {
  return {
    id: DRAFT_ID,
    entity_type: 'customer',
    entity_id: ENTITY_ID,
    google_account_id: null,
    to_addresses: ['ada@acme.it'],
    cc_addresses: [],
    subject: 'Offerta rivista',
    body_markdown: 'Gentile Ada,\n\necco la proposta.',
    attachment_version_ids: [],
    attachments: [],
    message_id_header: '<a.1@crm.example.it>',
    in_reply_to_message_id: null,
    send_state: 'bozza',
    send_attempted_at: null,
    last_error: null,
    sent_gmail_message_id: null,
    payment_reminder_id: null,
    created_at: '2026-08-20T09:00:00Z',
    updated_at: '2026-08-20T09:00:00Z',
    ...overrides,
  }
}

function drafts(...items: EmailDraftRead[]) {
  return ok({ items, total: items.length })
}

/** The person's own mailbox, as `GET /api/gmail/account` reports it. */
function health(overrides: { missing_scopes?: string[] } = {}) {
  return ok({
    account: {
      id: 'acc-1',
      email_address: 'io@example.it',
      scopes_granted: [SEND_SCOPE],
      status: 'active',
      consent_expires_at: null,
      last_error: null,
      last_error_at: null,
      last_sync_at: null,
    },
    banner: null,
    banner_text: null,
    missing_scopes: [],
    configured: true,
    ...overrides,
  })
}

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <EmailTab entityType="customer" entityId={ENTITY_ID} />
    </QueryClientProvider>,
  )
}

/**
 * Routes the three reads the tab makes: the correspondence, the drafts that have not left
 * (REB-415), and the person's own mailbox, which a draft reads for «Da» and for whether
 * it can send at all. Anything else is a defect.
 *
 * `drafts` may be a function, read on every request, so a test can change what the server
 * holds after a press and watch the tab read it again -- which is how every outcome of a
 * send reaches the screen.
 */
function respond(
  args: { messages?: unknown; drafts?: unknown | (() => unknown); account?: unknown } = {},
) {
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    if (path === '/api/gmail/messages') return Promise.resolve(args.messages ?? ok([]))
    if (path === '/api/email-drafts') {
      const current = typeof args.drafts === 'function' ? args.drafts() : args.drafts
      return Promise.resolve(current ?? drafts())
    }
    if (path === '/api/gmail/account') return Promise.resolve(args.account ?? health())
    throw new Error(`unexpected GET ${path}`)
  }) as never)
}

function postPaths(): string[] {
  return vi.mocked(api.POST).mock.calls.map((call) => call[0] as string)
}

function messagesCall() {
  return vi.mocked(api.GET).mock.calls.find((call) => call[0] === '/api/gmail/messages')
}

beforeEach(() => {
  mockAuth.role = 'admin'
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.DELETE).mockReset()
  vi.mocked(toast.success).mockReset()
  vi.mocked(toast.message).mockReset()
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('EmailTab', () => {
  it('asks only for this entity, and only for the stored mirror', async () => {
    respond({ messages: ok([message()]) })
    renderTab()

    expect(await screen.findByText('Rinnovo')).toBeInTheDocument()
    expect(messagesCall()?.[1]).toMatchObject({
      params: { query: { entity_type: 'customer', entity_id: ENTITY_ID } },
    })
    // And nothing on either read is a Gmail search string: both reach the CRM's own rows.
    await waitFor(() =>
      expect(new Set(vi.mocked(api.GET).mock.calls.map((call) => call[0]))).toEqual(
        new Set(['/api/gmail/messages', '/api/email-drafts']),
      ),
    )
  })

  it('groups messages by thread and shows the direction', async () => {
    respond({
      messages: ok([
        message({ id: 'a', gmail_message_id: 'm1' }),
        message({
          id: 'b',
          gmail_message_id: 'm2',
          direction: 'outbound',
          from_address: 'io@example.it',
        }),
        message({ id: 'c', gmail_message_id: 'm3', gmail_thread_id: 't2', subject: 'Altro' }),
      ]),
    })
    renderTab()

    expect(await screen.findByText('Rinnovo')).toBeInTheDocument()
    expect(screen.getAllByRole('group')).toHaveLength(2)
    // Two of the three fixtures are inbound, so this is `getAllByText`: the brief's
    // `getByText` would fail on its own fixture for being ambiguous, not for being
    // wrong about the component.
    expect(screen.getAllByText('Ricevuta')).toHaveLength(2)
    expect(screen.getByText('Inviata')).toBeInTheDocument()
  })

  /**
   * The conversation being worked on is the one that moved last. The order is computed
   * from the messages rather than taken from the response: the API orders by
   * `(gmail_thread_id, internal_date)` -- stable, but by an opaque Gmail id, so the
   * newest exchange would land wherever its thread id happened to sort.
   */
  it('puts the most recently active conversation first', async () => {
    respond({
      messages: ok([
        message({ id: 'a', gmail_thread_id: 't1', subject: 'Vecchia', internal_date: '2026-08-01T10:00:00Z' }),
        message({ id: 'b', gmail_thread_id: 't1', subject: 'Vecchia', internal_date: '2026-08-02T10:00:00Z' }),
        message({ id: 'c', gmail_thread_id: 't2', subject: 'Recente', internal_date: '2026-08-20T10:00:00Z' }),
      ]),
    })
    renderTab()

    await screen.findByText('Recente')
    expect(screen.getAllByRole('group').map((group) => group.getAttribute('aria-label'))).toEqual([
      'Recente',
      'Vecchia',
    ])
  })

  it('offers open in Gmail on every message', async () => {
    respond({ messages: ok([message()]) })
    renderTab()

    expect(await screen.findByRole('link', { name: 'Apri in Gmail' })).toHaveAttribute(
      'href',
      'https://mail.google.com/mail/u/0/#all/m1',
    )
  })

  /**
   * A failed read and an empty mailbox are two different claims, and the second one is
   * a lie when the first is what happened -- the defect `QueryErrorBanner` exists for.
   * The error branch is checked before the loading branch, because on an error
   * `isPending` is false while `data` is still undefined.
   */
  it('never renders an empty list for a failed request', async () => {
    respond({ messages: failed({ detail: 'database non raggiungibile' }, 503) })
    renderTab()

    expect(await screen.findByRole('alert')).toHaveTextContent('database non raggiungibile')
    expect(screen.queryByText(/Nessuna email/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Caricamento/)).not.toBeInTheDocument()
  })

  it('says plainly that there is nothing yet, when there genuinely is not', async () => {
    respond({ messages: ok([]) })
    renderTab()

    expect(await screen.findByText(/Nessuna email/)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('marks a message whose body was dropped or truncated, rather than showing a lie', async () => {
    respond({
      messages: ok([message({ body_truncated: true, body_html_scartato: true })]),
    })
    renderTab()

    expect(await screen.findByText(/troncato/)).toBeInTheDocument()
    expect(screen.getByText(/solo HTML/)).toBeInTheDocument()
  })

  /**
   * `gmail_store_bodies` off is a declared degradation, not a missing value: the
   * mailbox owner switched it off and only headers and Gmail's own snippet are kept.
   * Rendering that snippet as though it were the message would be the CRM claiming a
   * two-line email; saying which one it is costs a sentence.
   */
  it('shows the snippet, labelled as such, when no body was archived', async () => {
    respond({ messages: ok([message({ body_text: '', snippet: 'Anteprima' })]) })
    renderTab()

    expect(await screen.findByText('Anteprima')).toBeInTheDocument()
    expect(screen.getByText(/non è archiviato/)).toBeInTheDocument()
  })

  /**
   * Spec 5.4 stores no attachment bytes, and the endpoint that would fetch them from
   * Gmail and write them through `DocumentService` is not in this slice's REST surface.
   * The attachment is still named -- knowing an invoice was attached is most of the
   * value -- and the action that does not exist yet says so instead of failing.
   */
  it('names an attachment and does not offer an action that has no endpoint', async () => {
    respond({
      messages: ok([
        message({
          attachments: [{ filename: 'preventivo.pdf', mime: 'application/pdf', size: 20480 }],
        }),
      ]),
    })
    renderTab()

    expect(await screen.findByText(/preventivo\.pdf/)).toBeInTheDocument()
    const save = screen.getByRole('button', { name: 'Salva come documento' })
    expect(save).toBeDisabled()
    expect(save).toHaveAttribute('title', 'In arrivo')
  })

  // --- the drafts that have not left, and «Invia» (REB-415) ---------------------------

  /**
   * The person reads exactly what will leave before pressing anything: to, cc, subject,
   * the body as the plain text it is sent as, and each attachment by the file name the
   * recipient will see. A sent draft is not listed: it is in the correspondence now.
   */
  it('lists each unsent draft whole, above the correspondence', async () => {
    respond({
      messages: ok([message()]),
      drafts: drafts(
        draft({
          cc_addresses: ['amministrazione@acme.it'],
          attachment_version_ids: [VERSION_ID],
          attachments: [
            { version_id: VERSION_ID, filename: 'offerta-q1-v2.pdf', dimensione: 184_320 },
          ],
        }),
        draft({ id: 'sent', subject: 'Già partita', send_state: 'inviato' }),
      ),
    })
    renderTab()

    const card = await screen.findByRole('article', { name: 'Offerta rivista' })
    expect(within(card).getByText('ada@acme.it')).toBeInTheDocument()
    expect(within(card).getByText('amministrazione@acme.it')).toBeInTheDocument()
    // «Da» is the person's own mailbox, read from the health row the shell already keeps.
    expect(await within(card).findByText('io@example.it')).toBeInTheDocument()
    expect(within(card).getByLabelText('Testo')).toHaveTextContent('Gentile Ada, ecco la proposta.')
    expect(within(card).getByText(/offerta-q1-v2\.pdf · 180 KB/)).toBeInTheDocument()
    expect(within(card).getByText('Bozza')).toBeInTheDocument()
    expect(screen.queryByText('Già partita')).not.toBeInTheDocument()

    // Above the correspondence, never inside it: an unsent draft is not part of the
    // conversation the client has seen.
    const heading = screen.getByRole('heading', { name: 'Corrispondenza' })
    expect(card.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // The server leaves the sent ones out (`unsent`), so a page of them can never push an
    // older unsent draft off the list.
    expect(vi.mocked(api.GET).mock.calls.find((call) => call[0] === '/api/email-drafts')?.[1])
      .toMatchObject({
        params: { query: { entity_type: 'customer', entity_id: ENTITY_ID, unsent: true } },
      })
  })

  it('asks before sending, and sends nothing when the person steps back', async () => {
    respond({ drafts: drafts(draft()) })
    renderTab()

    await userEvent.click(await screen.findByRole('button', { name: 'Invia' }))

    const dialog = screen.getByRole('dialog', { name: 'Inviare questa email?' })
    expect(dialog).toHaveTextContent('non si può richiamare')
    await waitFor(() => expect(dialog).toHaveTextContent('io@example.it'))
    expect(dialog).toHaveTextContent('ada@acme.it')
    expect(api.POST).not.toHaveBeenCalled()

    await userEvent.click(within(dialog).getByRole('button', { name: 'Annulla' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(api.POST).not.toHaveBeenCalled()
  })

  it('sends on the confirmation, says so, and the draft leaves the list', async () => {
    let onServer: EmailDraftRead[] = [draft()]
    respond({ drafts: () => drafts(...onServer) })
    vi.mocked(api.POST).mockImplementation((() => {
      onServer = [draft({ send_state: 'inviato' })]
      return Promise.resolve(ok(draft({ send_state: 'inviato' })))
    }) as never)
    renderTab()

    await userEvent.click(await screen.findByRole('button', { name: 'Invia' }))
    await userEvent.click(screen.getByRole('button', { name: 'Invia ora' }))

    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith('Email inviata: la trovi nella corrispondenza.'),
    )
    expect(api.POST).toHaveBeenCalledWith('/api/email-drafts/{draft_id}/send', {
      params: { path: { draft_id: DRAFT_ID } },
    })
    await waitFor(() =>
      expect(screen.queryByRole('article', { name: 'Offerta rivista' })).not.toBeInTheDocument(),
    )
  })

  it('sends once and only once, so a double click cannot spend twice', async () => {
    respond({ drafts: drafts(draft()) })
    // Never settles: the second click has to be refused by the card itself, not by the
    // mutation happening to still be in flight when it arrives.
    vi.mocked(api.POST).mockReturnValue(new Promise(() => {}) as never)
    renderTab()

    await userEvent.click(await screen.findByRole('button', { name: 'Invia' }))
    // Two synchronous events, with no render between them: the button is not disabled
    // yet when the second one lands, so only the card's own guard can refuse it.
    const confirm = screen.getByRole('button', { name: 'Invia ora' })
    fireEvent.click(confirm)
    fireEvent.click(confirm)

    // The mutation starts its request a tick later, so wait for the first and then give
    // a second one every chance to follow.
    await waitFor(() => expect(api.POST).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(api.POST).toHaveBeenCalledTimes(1)
  })

  /**
   * While the send is in flight nothing else on the card can be pressed, and the dialog
   * cannot be dismissed: «Annulla» then «Elimina» is the natural panic move, and it would
   * race the send the person just confirmed.
   */
  it('holds the card still while the send is in flight', async () => {
    respond({ drafts: drafts(draft()) })
    vi.mocked(api.POST).mockReturnValue(new Promise(() => {}) as never)
    renderTab()

    await userEvent.click(await screen.findByRole('button', { name: 'Invia' }))
    await userEvent.click(screen.getByRole('button', { name: 'Invia ora' }))

    const dialog = screen.getByRole('dialog', { name: 'Inviare questa email?' })
    await waitFor(() =>
      expect(within(dialog).getByRole('button', { name: 'Annulla' })).toBeDisabled(),
    )
    await userEvent.keyboard('{Escape}')
    expect(screen.getByRole('dialog', { name: 'Inviare questa email?' })).toBeInTheDocument()
    const card = screen.getByRole('article', { name: 'Offerta rivista', hidden: true })
    expect(within(card).getByRole('button', { name: 'Elimina', hidden: true })).toBeDisabled()
  })

  /**
   * No answer at all is not a refusal: the request may have reached the API and the API
   * may have sent. The banner must not say «Riprova»; it says how to tell, from the row
   * that is read again, whether the draft left.
   */
  it('never tells the person to retry a send that got no answer', async () => {
    let reads = 0
    respond({
      drafts: () => {
        reads += 1
        return drafts(draft())
      },
    })
    vi.mocked(api.POST).mockResolvedValue(failed({ detail: 'Gateway Timeout' }, 504))
    renderTab()

    await userEvent.click(await screen.findByRole('button', { name: 'Invia' }))
    await userEvent.click(screen.getByRole('button', { name: 'Invia ora' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('non sappiamo se sia partita')
    expect(alert).not.toHaveTextContent(/riprova/i)
    // What «Bozza» proves is said only of the row read after the attempt, never of the
    // one cached before the press.
    await waitFor(() => expect(reads).toBe(2))
    await waitFor(() => expect(alert).toHaveTextContent('stato qui sopra è stato riletto'))
    expect(screen.getByRole('button', { name: 'Invia' })).toBeEnabled()
  })

  it('keeps «Invia» off until the draft has been read again after an unanswered send', async () => {
    let answer: (value: unknown) => void = () => {}
    let reads = 0
    respond({
      drafts: () => {
        reads += 1
        // The first read is the tab opening; the one after the failed press hangs, so
        // the card is caught with only the row from before the press.
        return reads === 1 ? drafts(draft()) : new Promise((resolve) => (answer = resolve))
      },
    })
    vi.mocked(api.POST).mockResolvedValue(failed({ detail: 'Gateway Timeout' }, 504))
    renderTab()

    await userEvent.click(await screen.findByRole('button', { name: 'Invia' }))
    await userEvent.click(screen.getByRole('button', { name: 'Invia ora' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Sto rileggendo')
    expect(screen.getByRole('button', { name: 'Invia' })).toBeDisabled()

    answer(drafts(draft()))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Invia' })).toBeEnabled())
    expect(screen.getByRole('alert')).toHaveTextContent('stato qui sopra è stato riletto')
  })

  /**
   * Gmail refused: nothing left, and the server says so on the row (`fallito`, with its
   * own sentence). The card reads the row again and shows that, with «Invia» still there
   * because the draft is intact -- and no second banner repeating it.
   */
  it('shows a refused send from the row, and offers «Invia» again', async () => {
    let onServer: EmailDraftRead[] = [draft()]
    respond({ drafts: () => drafts(...onServer) })
    vi.mocked(api.POST).mockImplementation((() => {
      onServer = [
        draft({
          send_state: 'fallito',
          last_error: 'Gmail ha rifiutato il messaggio: la bozza è intatta, correggila e riprova.',
        }),
      ]
      return Promise.resolve(
        failed(
          {
            code: 'conflict',
            detail: 'email_draft: Gmail ha rifiutato il messaggio',
            reason: 'Gmail ha rifiutato il messaggio',
            send_state: 'fallito',
          },
          409,
        ),
      )
    }) as never)
    renderTab()

    await userEvent.click(await screen.findByRole('button', { name: 'Invia' }))
    await userEvent.click(screen.getByRole('button', { name: 'Invia ora' }))

    expect(await screen.findByText('Invio non riuscito')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Gmail ha rifiutato il messaggio')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Invia' })).toBeEnabled()
    expect(toast.success).not.toHaveBeenCalled()
  })

  /** A refusal that did not touch the draft (no mailbox, a role, a network failure) is
   *  a banner in the person's language, never the log line `email_draft: ...`. */
  it('names a refusal that left the draft as it was', async () => {
    respond({ drafts: drafts(draft()) })
    vi.mocked(api.POST).mockResolvedValue(
      failed(
        {
          code: 'conflict',
          detail: 'google_account: nessuna casella Google collegata',
          reason: 'nessuna casella Google collegata',
        },
        409,
      ),
    )
    renderTab()

    await userEvent.click(await screen.findByRole('button', { name: 'Invia' }))
    await userEvent.click(screen.getByRole('button', { name: 'Invia ora' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/^Nessuna casella Google collegata$/)
    expect(screen.getByText('Bozza')).toBeInTheDocument()
  })

  /**
   * Gmail did not answer. The row is `incerto`, and the card must never call it
   * «inviata»: it says «esito da verificare», offers «Verifica», and offers no «Invia»
   * and no "riprova" -- the send has no idempotency key, so a second press is a second
   * email.
   */
  it('turns an unanswered send into «Esito da verificare», with no send again', async () => {
    let onServer: EmailDraftRead[] = [draft()]
    respond({ drafts: () => drafts(...onServer) })
    vi.mocked(api.POST).mockImplementation((() => {
      onServer = [draft({ send_state: 'incerto', last_error: 'Gmail non ha risposto.' })]
      return Promise.resolve(
        failed(
          {
            code: 'conflict',
            detail: "email_draft: esito dell'invio da verificare: Gmail non ha risposto",
            reason: "esito dell'invio da verificare: Gmail non ha risposto",
            send_state: 'incerto',
          },
          409,
        ),
      )
    }) as never)
    renderTab()

    await userEvent.click(await screen.findByRole('button', { name: 'Invia' }))
    await userEvent.click(screen.getByRole('button', { name: 'Invia ora' }))

    expect(await screen.findByText('Esito da verificare')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Verifica' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Invia' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /riprova/i })).not.toBeInTheDocument()
    expect(screen.queryByText(/inviata/i)).not.toBeInTheDocument()
    expect(postPaths()).toEqual(['/api/email-drafts/{draft_id}/send'])
  })

  it('verifies by asking Gmail, and never by posting the send again', async () => {
    let onServer: EmailDraftRead[] = [draft({ send_state: 'incerto' })]
    respond({ drafts: () => drafts(...onServer) })
    vi.mocked(api.POST).mockImplementation((() => {
      onServer = [draft({ send_state: 'inviato' })]
      return Promise.resolve(ok(draft({ send_state: 'inviato' })))
    }) as never)
    renderTab()

    await userEvent.click(await screen.findByRole('button', { name: 'Verifica' }))

    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        'L’email risulta partita: la trovi nella corrispondenza.',
      ),
    )
    expect(postPaths()).toEqual(['/api/email-drafts/{draft_id}/reconcile'])
    await waitFor(() =>
      expect(screen.queryByText('Esito da verificare')).not.toBeInTheDocument(),
    )
  })

  it('says «not yet» when Gmail cannot confirm it yet, and still offers no send', async () => {
    respond({ drafts: drafts(draft({ send_state: 'incerto' })) })
    vi.mocked(api.POST).mockResolvedValue(ok(draft({ send_state: 'incerto' })))
    renderTab()

    await userEvent.click(await screen.findByRole('button', { name: 'Verifica' }))

    await waitFor(() => expect(toast.message).toHaveBeenCalled())
    expect(vi.mocked(toast.message).mock.calls[0]?.[0]).toMatch(/senza rinviarla/)
    expect(screen.queryByRole('button', { name: 'Invia' })).not.toBeInTheDocument()
  })

  /**
   * `in_invio` is where a send whose request died after the claim is left. Without a
   * way out it would read «Invio in corso» forever; «Verifica» is that way, and it is
   * safe, since `reconcile` answers the draft unchanged while it may still be in flight.
   */
  it('offers «Verifica», and no send or delete, for a draft stuck in flight', async () => {
    respond({ drafts: drafts(draft({ send_state: 'in_invio' })) })
    renderTab()

    expect(await screen.findByText('Invio in corso')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Verifica' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Invia' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Elimina' })).not.toBeInTheDocument()
  })

  it('keeps «Invia» off for an attachment the send would refuse, and says so', async () => {
    respond({
      drafts: drafts(
        draft({
          attachment_version_ids: [VERSION_ID],
          attachments: [{ version_id: VERSION_ID, filename: null, dimensione: null }],
        }),
      ),
    })
    renderTab()

    expect(await screen.findByText(/Allegato non inviabile/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Invia' })).toBeDisabled()
    expect(screen.getByText(/Un allegato non si può più inviare/)).toBeInTheDocument()
  })

  it('names a refused delete and reads the draft again', async () => {
    respond({ drafts: drafts(draft()) })
    vi.mocked(api.DELETE).mockResolvedValue(
      failed(
        {
          code: 'conflict',
          detail: 'email_draft: questa email è già inviata o in invio: duplicala per modificarla',
          reason: 'questa email è già inviata o in invio: duplicala per modificarla',
          send_state: 'in_invio',
        },
        409,
      ),
    )
    renderTab()

    await userEvent.click(await screen.findByRole('button', { name: 'Elimina' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/^Questa email è già inviata/)
    await waitFor(() =>
      expect(
        vi.mocked(api.GET).mock.calls.filter((call) => call[0] === '/api/email-drafts'),
      ).toHaveLength(2),
    )
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('deletes a draft after asking, and not when the person says no', async () => {
    respond({ drafts: drafts(draft()) })
    vi.mocked(api.DELETE).mockResolvedValue(ok(undefined))
    vi.mocked(window.confirm).mockReturnValueOnce(false)
    renderTab()

    const remove = await screen.findByRole('button', { name: 'Elimina' })
    await userEvent.click(remove)
    expect(api.DELETE).not.toHaveBeenCalled()

    await userEvent.click(remove)

    await waitFor(() =>
      expect(api.DELETE).toHaveBeenCalledWith('/api/email-drafts/{draft_id}', {
        params: { path: { draft_id: DRAFT_ID } },
      }),
    )
    expect(window.confirm).toHaveBeenLastCalledWith(expect.stringMatching(/Eliminare questa bozza/))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Bozza eliminata'))
  })

  /**
   * REB-294's rule on this tab: a readonly person reads the draft whole and is offered
   * nothing to press, since the send, the verification and the delete all answer 403 to
   * that role (`require_write` in `gmail/send.py` and `gmail/drafts.py`).
   */
  it('shows a readonly person the draft and no button that the server would refuse', async () => {
    mockAuth.role = 'readonly'
    respond({
      drafts: drafts(draft(), draft({ id: 'unsure', subject: 'Esito', send_state: 'incerto' })),
    })
    renderTab()

    expect(await screen.findByRole('article', { name: 'Offerta rivista' })).toBeInTheDocument()
    expect(screen.getByRole('article', { name: 'Esito' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Invia' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Verifica' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Elimina' })).not.toBeInTheDocument()
  })

  it('lets a collaboratore send, verify and delete, as the server does', async () => {
    mockAuth.role = 'collaboratore'
    respond({
      drafts: drafts(draft(), draft({ id: 'unsure', subject: 'Esito', send_state: 'incerto' })),
    })
    renderTab()

    expect(await screen.findByRole('button', { name: 'Invia' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Verifica' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Elimina' })).toBeInTheDocument()
  })

  it('keeps «Invia» off, and says why, for a mailbox connected without sending', async () => {
    respond({ drafts: drafts(draft()), account: health({ missing_scopes: [SEND_SCOPE] }) })
    renderTab()

    await waitFor(() => expect(screen.getByRole('button', { name: 'Invia' })).toBeDisabled())
    expect(screen.getByText(/senza il permesso di invio/)).toBeInTheDocument()
  })

  /**
   * `draft_email` never attaches anything, so an assistant that writes «in allegato
   * l'offerta» produces a draft that promises a file it does not carry. A warning, not a
   * refusal: the text is the person's to judge.
   */
  it('warns when the text promises an attachment that is not there', async () => {
    respond({ drafts: drafts(draft({ body_markdown: "Gentile Ada,\n\nin allegato l'offerta." })) })
    renderTab()

    expect(await screen.findByText(/parla di un allegato/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Invia' })).toBeEnabled()
  })

  it('keeps showing the correspondence when the drafts read fails', async () => {
    // Two reads, two claims. A drafts read that failed must not take down the thread the
    // person actually came for.
    respond({
      messages: ok([message()]),
      drafts: failed({ detail: 'bozze non raggiungibili' }, 503),
    })
    renderTab()

    expect(await screen.findByText('Rinnovo')).toBeInTheDocument()
    expect(await screen.findByRole('alert')).toHaveTextContent('bozze non raggiungibili')
  })
})
