import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { toast } from '@rebase/ui/sonner'
import { ProposalDetail } from './ProposalDetail'
import { CONTRACT_ID, CONTRACT_PROPOSAL, CUSTOMER_ID, ok, proposal, refused, renderWithQuery } from './testing'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() } }
})

const navigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, params, children }: { to: string; params?: Record<string, string>; children: React.ReactNode }) => (
    <a href={params ? to.replace(/\$(\w+)/g, (_, key: string) => params[key] ?? '') : to}>{children}</a>
  ),
  useNavigate: () => navigate,
}))

const auth = { ruolo: 'collaboratore' as 'readonly' | 'collaboratore' | 'admin', nome: 'Lorenzo Fiore' }
vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ user: { id: 'u1', email: 'lorenzo@example.it', nome: auth.nome, ruolo: auth.ruolo } }),
  useCanWrite: () => auth.ruolo !== 'readonly',
}))

vi.mock('@rebase/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const NOT_FOUND = { title: 'x', status: 404, detail: 'proposal non trovato', code: 'not_found', instance: '/' }

function serving(shown = proposal(), timeline: unknown[] = []) {
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    if (path === '/api/proposals/{proposal_id}') return Promise.resolve(ok(shown))
    if (path === '/api/proposals/{proposal_id}/timeline') return Promise.resolve(ok(timeline))
    if (path === '/api/customers/{customer_id}') {
      return Promise.resolve(ok({ id: CUSTOMER_ID, ragione_sociale: 'Studio Rossi & Associati' }))
    }
    if (path === '/api/contracts/{contract_id}') {
      return Promise.resolve(ok({ id: CONTRACT_ID, titolo: 'Consulenza operativa 2026' }))
    }
    return Promise.resolve(ok(undefined))
  }) as never)
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(toast.success).mockReset()
  navigate.mockReset()
  auth.ruolo = 'collaboratore'
  auth.nome = 'Lorenzo Fiore'
})

describe('a proposal waiting for a decision', () => {
  it('shows the source excerpt, the agent’s confidence and every proposed field before the buttons', async () => {
    serving()
    renderWithQuery(<ProposalDetail proposalId="p1" />)

    expect(await screen.findByRole('heading', { level: 1, name: 'Giornata del 05/11/2026 · 1 gg' })).toBeInTheDocument()
    expect(screen.getByText(/Confermo la giornata di giovedì 5 novembre/)).toBeInTheDocument()
    expect(screen.getByText('Passo citato dal testo del documento.')).toBeInTheDocument()
    expect(screen.getByText(/Confidenza alta \(93%\)/)).toBeInTheDocument()
    expect(screen.getByText('Data e attività esplicite.')).toBeInTheDocument()
    expect(screen.getByText('Sopralluogo al magazzino di Verona')).toBeInTheDocument()
    expect(screen.getByText('giulia.bianchi@nordwind.example')).toBeInTheDocument()
    expect(await screen.findByText(/Sul contratto Consulenza operativa 2026/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Apri il documento/ })).toHaveAttribute('href', '/app/documents/d1')
    expect(screen.getByRole('button', { name: 'Accetta' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Rifiuta' })).toBeEnabled()
  })

  it('does not call a transcription a quotation', async () => {
    serving(proposal({ tipo_estratto: 'trascritto' }))
    renderWithQuery(<ProposalDetail proposalId="p1" />)
    expect(await screen.findByText(/non è verificabile sul documento/)).toBeInTheDocument()
  })

  it('links the customer a contract would be created for', async () => {
    serving(CONTRACT_PROPOSAL)
    renderWithQuery(<ProposalDetail proposalId="p2" />)
    const link = await screen.findByRole('link', { name: 'Studio Rossi & Associati' })
    expect(link).toHaveAttribute('href', `/app/customers/${CUSTOMER_ID}`)
  })

  it('accepts as proposed, in the reader’s name, and goes back to the queue', async () => {
    serving()
    vi.mocked(api.POST).mockResolvedValue(ok(proposal({ stato: 'accettata' })) as never)
    renderWithQuery(<ProposalDetail proposalId="p1" />)

    await userEvent.click(await screen.findByRole('button', { name: 'Accetta' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1))
    expect(api.POST).toHaveBeenCalledWith('/api/proposals/{proposal_id}/accept', {
      params: { path: { proposal_id: 'p1' } },
      body: {},
    })
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Proposta accettata: la giornata è registrata'))
    expect(navigate).toHaveBeenCalledWith({ to: '/app/proposals', search: { stato: 'in_attesa' } })
  })

  it('shows the server’s own refusal, stays on the proposal and does not leave the page', async () => {
    serving()
    vi.mocked(api.POST).mockResolvedValue(
      refused(409, {
        title: 'Conflitto',
        status: 409,
        detail: 'esiste già un day non concluso per questo contratto in questa data',
        code: 'conflict',
        instance: '/',
      }) as never,
    )
    renderWithQuery(<ProposalDetail proposalId="p1" />)

    await userEvent.click(await screen.findByRole('button', { name: 'Accetta' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Non è stato possibile accettare la proposta.')
    expect(alert).toHaveTextContent('esiste già un day non concluso per questo contratto in questa data')
    expect(navigate).not.toHaveBeenCalled()
    expect(toast.success).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Accetta' })).toBeEnabled()
  })

  it('rejects with the reason typed in the dialog', async () => {
    serving()
    vi.mocked(api.POST).mockResolvedValue(ok(proposal({ stato: 'rifiutata' })) as never)
    renderWithQuery(<ProposalDetail proposalId="p1" />)

    await userEvent.click(await screen.findByRole('button', { name: 'Rifiuta' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText('Motivo (facoltativo)'), '  La data non è quella della mail.  ')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Rifiuta la proposta' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1))
    expect(api.POST).toHaveBeenCalledWith('/api/proposals/{proposal_id}/reject', {
      params: { path: { proposal_id: 'p1' } },
      body: { motivo: 'La data non è quella della mail.' },
    })
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Proposta rifiutata'))
    expect(navigate).toHaveBeenCalledWith({ to: '/app/proposals', search: { stato: 'in_attesa' } })
  })

  it('rejects without a reason when none is given, sending null and not an empty string', async () => {
    serving()
    vi.mocked(api.POST).mockResolvedValue(ok(proposal({ stato: 'rifiutata' })) as never)
    renderWithQuery(<ProposalDetail proposalId="p1" />)

    await userEvent.click(await screen.findByRole('button', { name: 'Rifiuta' }))
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Rifiuta la proposta' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.POST).mock.calls[0]?.[1]).toMatchObject({ body: { motivo: null } })
  })

  it('leaves the dialog without sending anything on Annulla', async () => {
    serving()
    renderWithQuery(<ProposalDetail proposalId="p1" />)
    await userEvent.click(await screen.findByRole('button', { name: 'Rifiuta' }))
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Annulla' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(api.POST).not.toHaveBeenCalled()
  })

  it('offers a readonly reader nothing to press', async () => {
    auth.ruolo = 'readonly'
    serving()
    renderWithQuery(<ProposalDetail proposalId="p1" />)
    await screen.findByRole('heading', { level: 1 })
    expect(screen.queryByRole('button', { name: 'Accetta' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Rifiuta' })).toBeNull()
  })

  it('reads the proposal again when it was decided elsewhere first, and redraws it as decided', async () => {
    let decided = false
    vi.mocked(api.GET).mockImplementation(((path: string) => {
      if (path === '/api/proposals/{proposal_id}') {
        return Promise.resolve(
          ok(decided ? proposal({ stato: 'accettata', deciso_da: 'Ada', deciso_il: '2026-10-25T10:00:00Z' }) : proposal()),
        )
      }
      return Promise.resolve(ok([]))
    }) as never)
    vi.mocked(api.POST).mockImplementation((() => {
      decided = true
      return Promise.resolve(
        refused(409, {
          title: 'Conflitto',
          status: 409,
          detail: 'già decisa (accettata): una proposta si decide una sola volta',
          code: 'conflict',
          instance: '/',
        }),
      )
    }) as never)
    renderWithQuery(<ProposalDetail proposalId="p1" />)

    await userEvent.click(await screen.findByRole('button', { name: 'Accetta' }))

    expect(await screen.findByRole('region', { name: 'Esito' })).toHaveTextContent('Accettata da Ada')
    expect(screen.queryByRole('button', { name: 'Accetta' })).toBeNull()
    expect(navigate).not.toHaveBeenCalled()
  })

  it('keeps a refused rejection inside its dialog, once, and forgets it on Annulla', async () => {
    serving()
    vi.mocked(api.POST).mockResolvedValue(
      refused(500, { title: 'x', status: 500, detail: 'Il database non risponde.', code: 'internal', instance: '/' }) as never,
    )
    renderWithQuery(<ProposalDetail proposalId="p1" />)

    await userEvent.click(await screen.findByRole('button', { name: 'Rifiuta' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Rifiuta la proposta' }))

    const alerts = await screen.findAllByRole('alert')
    expect(alerts).toHaveLength(1)
    expect(dialog).toContainElement(alerts[0] ?? null)
    expect(alerts[0]).toHaveTextContent('Il database non risponde.')
    expect(navigate).not.toHaveBeenCalled()

    await userEvent.click(within(dialog).getByRole('button', { name: 'Annulla' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(screen.queryByRole('alert')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Rifiuta' }))
    expect(within(await screen.findByRole('dialog')).queryByRole('alert')).toBeNull()
  })
})

describe('a proposal already decided', () => {
  it('says who accepted it and when, and what it produced, with no buttons left', async () => {
    serving(
      proposal({
        stato: 'accettata',
        deciso_da: 'Ada Lovelace',
        deciso_il: '2026-10-25T10:00:00Z',
        id_risultato: 'w1',
      }),
    )
    renderWithQuery(<ProposalDetail proposalId="p1" />)
    const outcome = await screen.findByRole('region', { name: 'Esito' })
    expect(outcome).toHaveTextContent('Accettata da Ada Lovelace')
    expect(outcome).toHaveTextContent('Ha registrato la giornata come approvata.')
    expect(screen.queryByRole('button', { name: 'Accetta' })).toBeNull()
    expect(api.GET).not.toHaveBeenCalledWith('/api/proposals/{proposal_id}/timeline', expect.anything())
  })

  it('links the customer of an accepted contract', async () => {
    serving(proposal({ ...CONTRACT_PROPOSAL, stato: 'accettata', deciso_da: 'Ada', deciso_il: '2026-10-25T10:00:00Z' }))
    renderWithQuery(<ProposalDetail proposalId="p2" />)
    const outcome = await screen.findByRole('region', { name: 'Esito' })
    expect(within(outcome).getByRole('link', { name: 'Vai al cliente' })).toHaveAttribute(
      'href',
      `/app/customers/${CUSTOMER_ID}`,
    )
  })

  it('reads the reason of a rejection from the timeline, where the API keeps it', async () => {
    serving(proposal({ stato: 'rifiutata', deciso_da: 'Lorenzo Fiore', deciso_il: '2026-10-25T10:00:00Z' }), [
      { id: 'a2', kind: 'rejected', payload: { deciso_da: 'Lorenzo Fiore', motivo: 'La data non è quella della mail.' } },
      { id: 'a1', kind: 'created', payload: {} },
    ])
    renderWithQuery(<ProposalDetail proposalId="p1" />)
    expect(await screen.findByText('La data non è quella della mail.')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Esito' })).toHaveTextContent('Rifiutata da Lorenzo Fiore')
  })

  it('says so when the reason of a rejection could not be read, instead of showing none', async () => {
    vi.mocked(api.GET).mockImplementation(((path: string) =>
      Promise.resolve(
        path === '/api/proposals/{proposal_id}'
          ? ok(proposal({ stato: 'rifiutata', deciso_da: 'Lorenzo Fiore', deciso_il: '2026-10-25T10:00:00Z' }))
          : refused(500, { title: 'x', status: 500, detail: 'Il database non risponde.', code: 'internal', instance: '/' }),
      )) as never)
    renderWithQuery(<ProposalDetail proposalId="p1" />)
    expect(await screen.findByText(/Non è stato possibile leggere il motivo del rifiuto/)).toHaveTextContent(
      'Il database non risponde.',
    )
  })

  it('says nothing about a reason when a rejection had none', async () => {
    serving(proposal({ stato: 'rifiutata', deciso_da: 'Lorenzo Fiore', deciso_il: '2026-10-25T10:00:00Z' }), [
      { id: 'a2', kind: 'rejected', payload: { deciso_da: 'Lorenzo Fiore', motivo: null } },
    ])
    renderWithQuery(<ProposalDetail proposalId="p1" />)
    await screen.findByRole('region', { name: 'Esito' })
    await waitFor(() => expect(api.GET).toHaveBeenCalledWith('/api/proposals/{proposal_id}/timeline', expect.anything()))
    expect(screen.queryByText(/Motivo:/)).toBeNull()
  })
})

describe('a proposal that cannot be shown', () => {
  it('says it does not exist on a 404', async () => {
    vi.mocked(api.GET).mockResolvedValue(refused(404, NOT_FOUND) as never)
    renderWithQuery(<ProposalDetail proposalId="nope" />)
    expect(await screen.findByText('Proposta non trovata.')).toBeInTheDocument()
  })

  it('shows any other failure as one', async () => {
    vi.mocked(api.GET).mockResolvedValue(
      refused(500, { title: 'x', status: 500, detail: 'Il database non risponde.', code: 'internal', instance: '/' }) as never,
    )
    renderWithQuery(<ProposalDetail proposalId="p1" />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Il database non risponde.')
  })
})
