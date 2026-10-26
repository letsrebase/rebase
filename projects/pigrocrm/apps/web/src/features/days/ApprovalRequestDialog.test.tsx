import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ApprovalRequestDialog } from './ApprovalRequestDialog'
import { CONTRACT_ID, CUSTOMER_ID, ok, refused, renderWithQuery, row } from './testing'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() } }
})
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => <a href={to}>{children}</a>,
}))
vi.mock('@rebase/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
const gmail = { configured: true }
vi.mock('@/features/gmail/queries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/gmail/queries')>()
  return { ...actual, useGmailConfigured: () => gmail.configured }
})

const MESSAGE = {
  oggetto: 'Richiesta di approvazione delle giornate: Consulenza architetturale',
  corpo: 'Buongiorno,\n\n- lunedì 2 novembre 2026: giornata intera, Workshop\n\nGrazie.',
}
const DRAFT = {
  id: 'draft-1',
  updated_at: '2026-10-26T10:00:00.123456Z',
  send_state: 'bozza',
  to_addresses: ['amministrazione@acme.example'],
  last_error: null,
}

function serving() {
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    if (path === '/api/customers/{customer_id}') {
      return Promise.resolve(ok({ id: CUSTOMER_ID, ragione_sociale: 'Acme', email: 'amministrazione@acme.example' }))
    }
    return Promise.resolve(ok(undefined))
  }) as never)
}

function posts(sent: Record<string, unknown> = { ...DRAFT, send_state: 'inviato' }) {
  vi.mocked(api.POST).mockImplementation(((path: string) => {
    if (path === '/api/contracts/{contract_id}/approval-request/preview') return Promise.resolve(ok(MESSAGE))
    if (path === '/api/contracts/{contract_id}/approval-request') {
      return Promise.resolve(ok({ draft: DRAFT, work_units: [row({ id: 'p1', stato: 'proposto' }), row({ id: 'p2', stato: 'proposto' })] }))
    }
    if (path === '/api/email-drafts/{draft_id}/send') return Promise.resolve(ok(sent))
    return Promise.resolve(ok(undefined))
  }) as never)
}

/** The routes `POST`ed so far, in order. */
function postedPaths(): string[] {
  return (vi.mocked(api.POST).mock.calls as unknown as [string][]).map(([path]) => path)
}

function open() {
  return renderWithQuery(
    <ApprovalRequestDialog open onOpenChange={vi.fn()} contractId={CONTRACT_ID} customerId={CUSTOMER_ID} />,
  )
}

async function toTheText(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('Cosa farai'), 'Workshop')
  await user.click(screen.getByRole('button', { name: 'Leggi la richiesta' }))
  await screen.findByLabelText('Messaggio')
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  gmail.configured = true
  serving()
  posts()
})

describe('asking a client for approval', () => {
  it('previews the request for the dates, to the customer’s address, and sends exactly what was read', async () => {
    const user = userEvent.setup()
    open()
    await toTheText(user)

    expect(screen.getByLabelText('Oggetto')).toHaveValue(MESSAGE.oggetto)
    expect(screen.getByLabelText('Messaggio')).toHaveValue(MESSAGE.corpo)
    await waitFor(() => expect(screen.getByLabelText('A')).toHaveValue('amministrazione@acme.example'))
    await user.click(screen.getByRole('button', { name: 'Invia la richiesta' }))

    await screen.findByText(/in attesa della risposta del cliente/)
    const calls = postedPaths()
    expect(calls).toEqual([
      '/api/contracts/{contract_id}/approval-request/preview',
      '/api/contracts/{contract_id}/approval-request',
      '/api/email-drafts/{draft_id}/send',
    ])
    expect(vi.mocked(api.POST).mock.calls[1]?.[1]).toMatchObject({
      body: {
        to_addresses: ['amministrazione@acme.example'],
        oggetto: MESSAGE.oggetto,
        corpo: MESSAGE.corpo,
        days: [{ quantita: '1.00', descrizione: 'Workshop' }],
      },
    })
    // The send names the revision that was read, untouched.
    expect(vi.mocked(api.POST).mock.calls[2]?.[1]).toMatchObject({
      params: { path: { draft_id: 'draft-1' } },
      body: { updated_at: DRAFT.updated_at },
    })
    expect(screen.getByText(/Le 2 giornate sono registrate come proposte/)).toBeInTheDocument()
  })

  it('sends the text the person edited, not the one it was given', async () => {
    const user = userEvent.setup()
    open()
    await toTheText(user)
    const body = screen.getByLabelText('Messaggio')
    await user.clear(body)
    await user.type(body, 'Testo mio')
    await user.click(screen.getByRole('button', { name: 'Invia la richiesta' }))
    await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(3))
    expect(vi.mocked(api.POST).mock.calls[1]?.[1]).toMatchObject({ body: { corpo: 'Testo mio' } })
  })

  it('keeps the draft and the days when the send fails, and sends the same draft again', async () => {
    posts({ ...DRAFT, send_state: 'fallito', last_error: 'Gmail ha rifiutato la richiesta' })
    const user = userEvent.setup()
    open()
    await toTheText(user)
    await user.click(screen.getByRole('button', { name: 'Invia la richiesta' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Gmail ha rifiutato la richiesta')

    posts()
    await user.click(screen.getByRole('button', { name: 'Riprova l’invio' }))
    await screen.findByText(/in attesa della risposta del cliente/)
    // The retry sent the draft that already existed: no second request was prepared.
    const prepared = postedPaths().filter((path) => path === '/api/contracts/{contract_id}/approval-request')
    expect(prepared).toHaveLength(1)
  })

  it('does not offer a retry for an outcome nobody knows', async () => {
    posts({ ...DRAFT, send_state: 'incerto' })
    const user = userEvent.setup()
    open()
    await toTheText(user)
    await user.click(screen.getByRole('button', { name: 'Invia la richiesta' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/Non sappiamo se la richiesta è partita/)
    expect(screen.queryByRole('button', { name: 'Riprova l’invio' })).not.toBeInTheDocument()
  })

  it('only prepares the draft when the mailbox is not connected, and says so', async () => {
    gmail.configured = false
    const user = userEvent.setup()
    open()
    await toTheText(user)
    expect(screen.getByText(/La posta non è collegata/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Prepara la bozza' }))
    await screen.findByText(/La richiesta è pronta/)
    expect(postedPaths()).not.toContain('/api/email-drafts/{draft_id}/send')
  })

  it('shows the server’s sentence when a date is already held, and nothing was sent', async () => {
    vi.mocked(api.POST).mockImplementation(((path: string) => {
      if (path.endsWith('/preview')) return Promise.resolve(ok(MESSAGE))
      return Promise.resolve(
        refused(409, { title: 'x', status: 409, detail: 'il contratto ha gia\' una giornata in queste date: 2026-11-03', code: 'conflict', instance: '/' }),
      )
    }) as never)
    const user = userEvent.setup()
    open()
    await toTheText(user)
    await user.click(screen.getByRole('button', { name: 'Invia la richiesta' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/gia' una giornata in queste date/)
    expect(postedPaths()).not.toContain('/api/email-drafts/{draft_id}/send')
  })

  it('adds and removes dates, and refuses the same date twice', async () => {
    const user = userEvent.setup()
    open()
    await user.click(screen.getByRole('button', { name: 'Aggiungi una data' }))
    const dates = screen.getAllByLabelText(/^Data \d/)
    expect(dates).toHaveLength(2)
    await user.type(screen.getAllByLabelText('Cosa farai')[0] as HTMLElement, 'A')
    await user.type(screen.getAllByLabelText('Cosa farai')[1] as HTMLElement, 'B')
    // Both start on tomorrow: the same date twice is said and cannot be read.
    expect(screen.getByText('Una data è indicata due volte.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Leggi la richiesta' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Togli la data 2' }))
    expect(screen.queryByText('Una data è indicata due volte.')).not.toBeInTheDocument()
    expect(within(screen.getByRole('dialog')).getAllByLabelText(/^Data \d/)).toHaveLength(1)
  })
})
