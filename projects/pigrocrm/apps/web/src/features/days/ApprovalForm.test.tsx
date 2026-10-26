import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ApprovalForm } from './ApprovalForm'
import { CONTRACT_ID, CUSTOMER_ID, contract, ok, refused, renderWithQuery, row } from './testing'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() }, fetchWithRefresh: vi.fn() }
})

const navigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => <a href={to}>{children}</a>,
  useNavigate: () => navigate,
}))
const auth = { ruolo: 'collaboratore' as 'readonly' | 'collaboratore' }
vi.mock('@/lib/auth', () => ({ useCanWrite: () => auth.ruolo !== 'readonly' }))
vi.mock('@rebase/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const gmail = { configured: true }
vi.mock('@/features/gmail/queries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/gmail/queries')>()
  return { ...actual, useGmailConfigured: () => gmail.configured }
})

const MESSAGE = {
  id: 'msg-1',
  gmail_message_id: 'g1',
  gmail_thread_id: 't1',
  direction: 'inbound',
  from_address: 'giulia@cliente.example',
  to_addresses: ['me@example.com'],
  cc_addresses: [],
  subject: 'Re: giornate di marzo',
  snippet: '',
  internal_date: '2026-03-01T09:12:00Z',
  body_text: 'Buongiorno, confermo le giornate del 2 e del 3 marzo.',
  body_truncated: false,
  body_html_scartato: false,
  attachments: [],
}

function serving(messages: unknown[] = [MESSAGE]) {
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    if (path === '/api/contracts') return Promise.resolve(ok({ items: [contract({ customer_id: CUSTOMER_ID })], next_cursor: null }))
    if (path === '/api/work-units') {
      return Promise.resolve(
        ok({
          items: [
            row({ id: 'd1', data: '2026-03-02', stato: 'proposto', descrizione: 'Workshop' }),
            row({ id: 'd2', data: '2026-03-03', stato: 'lavorato_senza_approvazione', descrizione: 'Review' }),
            row({ id: 'd3', data: '2026-03-04', stato: 'lavorato', descrizione: 'Già a posto' }),
          ],
          troncato: false,
        }),
      )
    }
    if (path === '/api/gmail/messages') return Promise.resolve(ok(messages))
    return Promise.resolve(ok(undefined))
  }) as never)
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  navigate.mockReset()
  auth.ruolo = 'collaboratore'
  gmail.configured = true
})

describe('recording an approval', () => {
  it('offers only the days that can use an approval, with the ones it was opened for ticked', async () => {
    serving()
    renderWithQuery(<ApprovalForm initialContractId={CONTRACT_ID} initialDayIds={['d1']} />)

    expect(await screen.findByRole('checkbox', { name: /02\/03\/2026, Workshop/ })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /03\/03\/2026, Review/ })).not.toBeChecked()
    expect(screen.queryByRole('checkbox', { name: /Già a posto/ })).not.toBeInTheDocument()
  })

  it('starts on the synced email when a mailbox is connected and on the file when none is', async () => {
    serving()
    renderWithQuery(<ApprovalForm initialContractId={CONTRACT_ID} initialDayIds={['d1']} />)
    expect(await screen.findByRole('radio', { name: 'Un messaggio sincronizzato' })).toBeChecked()
  })

  it('records an approval from a synced email and the days it names, then opens the contract’s days', async () => {
    serving()
    vi.mocked(api.POST).mockResolvedValue(
      ok({ approval: { id: 'a1' }, work_units: [row({ id: 'd1', stato: 'approvato' }), row({ id: 'd2', stato: 'lavorato' })] }) as never,
    )
    const user = userEvent.setup()
    renderWithQuery(<ApprovalForm initialContractId={CONTRACT_ID} initialDayIds={['d1', 'd2']} />)

    await user.click(await screen.findByRole('radio', { name: /Re: giornate di marzo/ }))
    // The message tells who sent it and when, and its text is there to copy from.
    expect(screen.getByLabelText('Da chi')).toHaveValue('giulia@cliente.example')
    expect(screen.getByText(/confermo le giornate del 2 e del 3 marzo/)).toBeInTheDocument()
    await user.type(screen.getByLabelText(/La frase che approva/), 'confermo le giornate del 2 e del 3 marzo.')
    await user.click(screen.getByRole('button', { name: 'Registra approvazione per 2 giornate' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.POST).mock.calls[0]?.[0]).toBe('/api/approvals')
    expect(vi.mocked(api.POST).mock.calls[0]?.[1]).toMatchObject({
      body: {
        contract_id: CONTRACT_ID,
        canale: 'email',
        mittente: 'giulia@cliente.example',
        estratto: 'confermo le giornate del 2 e del 3 marzo.',
        gmail_message_id: 'msg-1',
        work_unit_ids: ['d1', 'd2'],
      },
    })
    await waitFor(() => expect(navigate).toHaveBeenCalledWith({ to: '/app/days', search: { contract: CONTRACT_ID } }))
  })

  it('says when the excerpt is not in the chosen message, and still lets the person go on', async () => {
    serving()
    const user = userEvent.setup()
    renderWithQuery(<ApprovalForm initialContractId={CONTRACT_ID} initialDayIds={['d1']} />)
    await user.click(await screen.findByRole('radio', { name: /Re: giornate di marzo/ }))
    await user.type(screen.getByLabelText(/La frase che approva/), 'una frase mai scritta')
    expect(screen.getByRole('status')).toHaveTextContent(/non compare nel messaggio scelto/)
    expect(screen.getByRole('button', { name: 'Registra approvazione' })).toBeEnabled()
  })

  it('cannot be saved without a day, a proof and the excerpt', async () => {
    serving()
    renderWithQuery(<ApprovalForm initialContractId={CONTRACT_ID} initialDayIds={[]} />)
    await screen.findByRole('checkbox', { name: /02\/03\/2026, Workshop/ })
    expect(screen.getByRole('button', { name: 'Registra approvazione' })).toBeDisabled()
  })

  it('files pasted text as a document of the contract and records the approval on it', async () => {
    gmail.configured = false
    serving()
    const { fetchWithRefresh } = await import('@/lib/api')
    vi.mocked(fetchWithRefresh).mockResolvedValue(new Response(JSON.stringify({ id: 'v1' }), { status: 201 }))
    vi.mocked(api.POST).mockImplementation(((path: string) => {
      if (path === '/api/documents') return Promise.resolve(ok({ id: 'doc-7' }))
      return Promise.resolve(ok({ approval: { id: 'a1' }, work_units: [row({ id: 'd1', stato: 'approvato' })] }))
    }) as never)
    const user = userEvent.setup()
    renderWithQuery(<ApprovalForm initialContractId={CONTRACT_ID} initialDayIds={['d1']} />)

    await user.click(await screen.findByRole('radio', { name: 'Testo incollato' }))
    await user.type(screen.getByLabelText('Testo dell’approvazione'), 'Confermo la giornata del 2 marzo.')
    await user.type(screen.getByLabelText('Da chi'), 'giulia@cliente.example')
    await user.type(screen.getByLabelText(/La frase che approva/), 'Confermo la giornata del 2 marzo.')
    await user.click(screen.getByRole('button', { name: 'Registra approvazione' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(2))
    expect(vi.mocked(api.POST).mock.calls[0]?.[0]).toBe('/api/documents')
    expect(vi.mocked(api.POST).mock.calls[0]?.[1]).toMatchObject({
      body: { contract_id: CONTRACT_ID, tipo: 'documento' },
    })
    expect(vi.mocked(fetchWithRefresh).mock.calls[0]?.[0]).toBe('/api/documents/doc-7/versions')
    expect(vi.mocked(api.POST).mock.calls[1]?.[1]).toMatchObject({ body: { document_id: 'doc-7', work_unit_ids: ['d1'] } })
    await waitFor(() => expect(navigate).toHaveBeenCalledWith({ to: '/app/days/$workUnitId', params: { workUnitId: 'd1' } }))
  })

  it('shows the server’s own sentence when it refuses a day, and records nothing', async () => {
    serving()
    vi.mocked(api.POST).mockResolvedValue(
      refused(409, { title: 'x', status: 409, detail: "queste giornate non possono ricevere un'approvazione: 2026-03-02 (approvato)", code: 'conflict', instance: '/' }) as never,
    )
    const user = userEvent.setup()
    renderWithQuery(<ApprovalForm initialContractId={CONTRACT_ID} initialDayIds={['d1']} />)
    await user.click(await screen.findByRole('radio', { name: /Re: giornate di marzo/ }))
    await user.type(screen.getByLabelText(/La frase che approva/), 'confermo')
    await user.click(screen.getByRole('button', { name: 'Registra approvazione' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/non possono ricevere/)
    expect(navigate).not.toHaveBeenCalled()
  })

  it('says there is no mailbox, and offers the file and the pasted text instead', async () => {
    gmail.configured = false
    serving()
    renderWithQuery(<ApprovalForm initialContractId={CONTRACT_ID} initialDayIds={['d1']} />)
    expect(await screen.findByLabelText('File')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Un file' })).toBeChecked()
    await userEvent.click(screen.getByRole('radio', { name: 'Un messaggio sincronizzato' }))
    expect(screen.getByText(/La posta non è collegata/)).toBeInTheDocument()
  })

  it('says when no message from the client was synced', async () => {
    serving([])
    renderWithQuery(<ApprovalForm initialContractId={CONTRACT_ID} initialDayIds={['d1']} />)
    expect(await screen.findByText(/Nessun messaggio ricevuto da questo cliente/)).toBeInTheDocument()
  })

  it('offers a reader nothing to record', () => {
    auth.ruolo = 'readonly'
    serving()
    renderWithQuery(<ApprovalForm initialDayIds={[]} />)
    expect(screen.getByText(/puoi leggere le approvazioni ma non registrarne/)).toBeInTheDocument()
    expect(within(document.body).queryByRole('button', { name: /Registra approvazione/ })).not.toBeInTheDocument()
  })
})
