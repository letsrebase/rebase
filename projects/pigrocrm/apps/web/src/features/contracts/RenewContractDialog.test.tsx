import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { formatDate } from '@/features/invoices/format'
import { api } from '@/lib/api'
import { toast } from '@rebase/ui/sonner'
import { RenewContractButton } from './RenewContractDialog'

const navigate = vi.fn()
vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return { ...actual, useNavigate: () => navigate }
})

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), PATCH: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

const auth = vi.hoisted(() => ({ canRenew: true }))
vi.mock('@/lib/auth', () => ({ useCan: () => auth.canRenew }))

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 201 }) }) as never
}
function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) }) as never
}

const CONTRACT = {
  id: 'c1',
  titolo: 'Sviluppo piattaforma',
  fine: '2026-12-31',
  contratto_successivo_id: null,
}

function renderButton(contract: Parameters<typeof RenewContractButton>[0]['contract'] = CONTRACT) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <RenewContractButton contract={contract} />
    </QueryClientProvider>,
  )
}

async function openDialog() {
  await userEvent.click(screen.getByRole('button', { name: 'Rinnova' }))
  return screen.findByRole('dialog')
}

/** The dialog's own button: with it open the page behind is hidden from the tree, so the
 *  name «Rinnova» is the dialog's alone. */
async function submit() {
  await userEvent.click(screen.getByRole('button', { name: 'Rinnova' }))
}

/** The body the dialog POSTed, which is the only thing these tests are about. */
function posted() {
  const call = vi.mocked(api.POST).mock.calls[0] as unknown as [
    string,
    { params: { path: { contract_id: string } }; body: unknown },
  ]
  return { path: call[0], contractId: call[1].params.path.contract_id, body: call[1].body }
}

beforeEach(() => {
  vi.mocked(api.POST).mockReset()
  vi.mocked(toast.success).mockReset()
  navigate.mockReset()
  auth.canRenew = true
})

describe('RenewContractButton', () => {
  it('offers Rinnova on a contract with an end and no successor', () => {
    renderButton()
    expect(screen.getByRole('button', { name: 'Rinnova' })).toBeInTheDocument()
  })

  it('offers nothing on a contract without an end', () => {
    renderButton({ ...CONTRACT, fine: null })
    expect(screen.queryByRole('button', { name: 'Rinnova' })).not.toBeInTheDocument()
  })

  it('offers nothing on a contract that was already renewed', () => {
    renderButton({ ...CONTRACT, contratto_successivo_id: 'c2' })
    expect(screen.queryByRole('button', { name: 'Rinnova' })).not.toBeInTheDocument()
  })

  it('offers nothing to someone who cannot renew', () => {
    auth.canRenew = false
    renderButton()
    expect(screen.queryByRole('button', { name: 'Rinnova' })).not.toBeInTheDocument()
  })
})

describe('RenewContractDialog', () => {
  it('names the day the successor starts and takes the current title as the hint', async () => {
    renderButton()
    const dialog = await openDialog()

    expect(dialog).toHaveTextContent('Rinnova il contratto')
    expect(dialog).toHaveTextContent(
      `Il nuovo contratto ha gli stessi termini e comincia il ${formatDate('2027-01-01')}.`,
    )
    expect(screen.getByLabelText('Titolo')).toHaveAttribute('placeholder', 'Sviluppo piattaforma')
  })

  it('sends an empty body when nothing is filled in, then navigates to the successor', async () => {
    vi.mocked(api.POST).mockReturnValue(ok({ id: 'c2' }))
    renderButton()
    await openDialog()
    await submit()

    expect(posted()).toEqual({
      path: '/api/contracts/{contract_id}/renew',
      contractId: 'c1',
      body: {},
    })
    await vi.waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: '/app/contracts/$contractId',
        params: { contractId: 'c2' },
      }),
    )
    expect(toast.success).toHaveBeenCalledWith('Contratto rinnovato')
  })

  it('sends the title and the start when they are given', async () => {
    vi.mocked(api.POST).mockReturnValue(ok({ id: 'c2' }))
    renderButton()
    await openDialog()
    await userEvent.type(screen.getByLabelText('Titolo'), '  Sviluppo piattaforma 2027 ')
    fireEvent.change(screen.getByLabelText('Inizio'), { target: { value: '2027-02-01' } })
    await submit()

    expect(posted().body).toEqual({
      titolo: 'Sviluppo piattaforma 2027',
      inizio: '2027-02-01',
    })
  })

  it('sends a duration as a count and a unit', async () => {
    vi.mocked(api.POST).mockReturnValue(ok({ id: 'c2' }))
    renderButton()
    await openDialog()
    await userEvent.click(screen.getByRole('combobox', { name: 'Fine' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Durata' }))
    await userEvent.clear(screen.getByLabelText('Quanto dura'))
    await userEvent.type(screen.getByLabelText('Quanto dura'), '18')
    await userEvent.click(screen.getByRole('combobox', { name: 'Unità' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Settimane' }))
    await submit()

    expect(posted().body).toEqual({ durata: { conteggio: 18, unita: 'settimane' } })
  })

  it('sends an end date as `fine`, never together with a duration', async () => {
    vi.mocked(api.POST).mockReturnValue(ok({ id: 'c2' }))
    renderButton()
    await openDialog()
    await userEvent.click(screen.getByRole('combobox', { name: 'Fine' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Data di fine' }))
    fireEvent.change(screen.getByLabelText('Data di fine'), { target: { value: '2027-12-31' } })
    await submit()

    expect(posted().body).toEqual({ fine: '2027-12-31' })
  })

  it('reads a 409 inside the dialog and stays open without navigating', async () => {
    vi.mocked(api.POST).mockReturnValue(
      failed(
        {
          type: 'about:blank',
          title: 'Conflict',
          status: 409,
          code: 'conflict',
          detail: 'Il contratto è già stato rinnovato.',
          instance: '/api/contracts/c1/renew',
        },
        409,
      ),
    )
    renderButton()
    const dialog = await openDialog()
    await submit()

    expect(await screen.findByText('Il contratto è già stato rinnovato.')).toBeInTheDocument()
    expect(dialog).toBeInTheDocument()
    expect(navigate).not.toHaveBeenCalled()
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('reads a 422 about a contract without an end inside the dialog', async () => {
    vi.mocked(api.POST).mockReturnValue(
      failed(
        {
          type: 'about:blank',
          title: 'Unprocessable',
          status: 422,
          code: 'contract_has_no_end',
          detail: 'Un contratto senza fine non si rinnova.',
          instance: '/api/contracts/c1/renew',
        },
        422,
      ),
    )
    renderButton()
    await openDialog()
    await submit()

    expect(await screen.findByText('Un contratto senza fine non si rinnova.')).toBeInTheDocument()
    expect(navigate).not.toHaveBeenCalled()
  })
})
