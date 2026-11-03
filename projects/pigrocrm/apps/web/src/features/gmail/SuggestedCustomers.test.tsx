/**
 * The list of customers the connected mailbox proposes (REB-223), driven as a person
 * drives it. The API is stubbed at `api.GET`/`api.POST`, answering `{ data | error,
 * response }` so `unwrap` does the throwing, as it does live.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { SuggestedCustomers } from './SuggestedCustomers'
import type { SuggestedCustomer } from './suggestions'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() } }
})

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('@rebase/ui/sonner', () => ({ toast }))

const PROPOSALS: SuggestedCustomer[] = [
  {
    domain: 'acme.it',
    name: 'Acme',
    conversations: 4,
    last_message: '2026-09-20T09:30:00Z',
    people: [
      { address: 'marco@acme.it', name: 'Marco Bianchi', already_in_directory: false },
      { address: 'sara@acme.it', name: '', already_in_directory: false },
      { address: 'luca@acme.it', name: 'Luca', already_in_directory: true },
    ],
  },
  {
    domain: 'studio-rossi.it',
    name: 'Studio Rossi',
    conversations: 1,
    last_message: null,
    people: [{ address: 'anna@studio-rossi.it', name: 'Anna', already_in_directory: false }],
  },
]

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

function failed(detail: string, status: number) {
  return Promise.resolve({
    error: { detail, status, title: 'Conflict', code: 'conflict' },
    response: new Response(null, { status }),
  }) as never
}

function renderList(onImported = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <SuggestedCustomers onImported={onImported} />
    </QueryClientProvider>,
  )
  return onImported
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  toast.success.mockReset()
  toast.error.mockReset()
})

describe('the proposed customers', () => {
  it('lists every proposal unticked, with what the mailbox knows of it', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(PROPOSALS))
    renderList()
    const list = await screen.findByRole('list', { name: 'Clienti proposti dalla casella' })
    expect(within(list).getByText('Acme')).toBeInTheDocument()
    expect(within(list).getByText('acme.it')).toBeInTheDocument()
    expect(within(list).getByText(/4 conversazioni, l’ultima il/)).toBeInTheDocument()
    expect(within(list).getByText('1 conversazione')).toBeInTheDocument()
    expect(within(list).getByText('Marco Bianchi <marco@acme.it>')).toBeInTheDocument()
    expect(within(list).getByText(/già in anagrafica/)).toBeInTheDocument()
    // Nothing is ticked at first, so nothing can be imported yet.
    expect(screen.getByRole('checkbox', { name: /Acme/ })).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Importa i clienti spuntati' })).toBeDisabled()
    expect(api.GET).toHaveBeenCalledWith('/api/gmail/customer-suggestions')
  })

  it('imports what was ticked, with the corrected name and the chosen people', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(PROPOSALS))
    vi.mocked(api.POST).mockImplementation(() => ok([{ id: 'c1' }]))
    const user = userEvent.setup()
    const onImported = renderList()

    await user.click(await screen.findByRole('checkbox', { name: /Acme/ }))
    const name = screen.getByLabelText('Nome del cliente per acme.it')
    expect(name).toHaveValue('Acme')
    await user.clear(name)
    await user.type(name, 'Acme S.r.l.')
    // The people not yet in the address book come ticked; one is left out.
    const sara = screen.getByRole('checkbox', { name: 'sara@acme.it' })
    expect(sara).toBeChecked()
    await user.click(sara)
    expect(screen.queryByRole('checkbox', { name: /Luca/ })).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Importa 1 cliente' }))

    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/customers/from-suggestions', {
        body: {
          customers: [
            {
              domain: 'acme.it',
              legal_name: 'Acme S.r.l.',
              people: [{ address: 'marco@acme.it', name: 'Marco Bianchi' }],
            },
          ],
        },
      }),
    )
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('1 cliente importato'))
    expect(onImported).toHaveBeenCalledWith(1)
  })

  it('will not import a customer whose name was emptied', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(PROPOSALS))
    const user = userEvent.setup()
    renderList()
    await user.click(await screen.findByRole('checkbox', { name: /Studio Rossi/ }))
    await user.clear(screen.getByLabelText('Nome del cliente per studio-rossi.it'))
    expect(screen.getByRole('button', { name: 'Importa 1 cliente' })).toBeDisabled()
  })

  it('says the server’s sentence when the import is refused', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(PROPOSALS))
    vi.mocked(api.POST).mockImplementation(() => failed('customer: acme.it è già un cliente', 409))
    const user = userEvent.setup()
    const onImported = renderList()
    await user.click(await screen.findByRole('checkbox', { name: /Acme/ }))
    await user.click(screen.getByRole('button', { name: 'Importa 1 cliente' }))
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('customer: acme.it è già un cliente'))
    expect(onImported).not.toHaveBeenCalled()
  })

  it('says when there is nobody to propose', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok([]))
    renderList()
    expect(await screen.findByText(/Nessuna azienda da proporre/)).toBeInTheDocument()
  })

  it('shows why the mailbox could not be read', async () => {
    vi.mocked(api.GET).mockImplementation(() =>
      failed('Il consenso Google per ada@acme.it è stato revocato: ricollega la casella.', 409),
    )
    renderList()
    expect(await screen.findByRole('alert')).toHaveTextContent(/è stato revocato/)
  })
})
