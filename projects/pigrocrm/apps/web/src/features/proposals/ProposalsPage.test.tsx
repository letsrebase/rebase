import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ProposalsPage } from './ProposalsPage'
import { CONTRACT_ID, CONTRACT_PROPOSAL, CUSTOMER_ID, ok, proposal, refused, renderWithQuery } from './testing'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() } }
})

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, params, children, ...rest }: { to: string; params?: Record<string, string>; children: React.ReactNode }) => (
    <a href={params ? to.replace(/\$(\w+)/g, (_, key: string) => params[key] ?? '') : to} {...rest}>
      {children}
    </a>
  ),
}))

const PENDING = [proposal(), CONTRACT_PROPOSAL]

function answering(byStatus: Record<string, unknown[]>, failing: string[] = []) {
  vi.mocked(api.GET).mockImplementation(((path: string, init?: { params?: { query?: { status?: string } } }) => {
    if (path === '/api/proposals') {
      const status = init?.params?.query?.status ?? 'pending'
      if (failing.includes(status)) return Promise.resolve(refused(500, { detail: 'Il database non risponde.', code: 'internal', title: 'x', status: 500, instance: '/' }))
      return Promise.resolve(ok({ items: byStatus[status] ?? [] }))
    }
    if (path === '/api/customers/{customer_id}') {
      return Promise.resolve(ok({ id: CUSTOMER_ID, legal_name: 'Studio Rossi & Associati' }))
    }
    if (path === '/api/contracts/{contract_id}') {
      return Promise.resolve(ok({ id: CONTRACT_ID, title: 'Consulenza operativa 2026' }))
    }
    return Promise.resolve(ok(undefined))
  }) as never)
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
})

describe('the Proposte queue', () => {
  it('lists what waits, oldest first, with the kind, the subject and the excerpt', async () => {
    answering({ pending: PENDING })
    renderWithQuery(<ProposalsPage status="pending" onStatusChange={vi.fn()} />)

    expect(await screen.findByText('Giornata del 05/11/2026 · 1 gg')).toBeInTheDocument()
    expect(screen.getByText('Consulenza fiscale 2027')).toBeInTheDocument()
    expect(await screen.findByText('Consulenza operativa 2026')).toBeInTheDocument()
    expect(await screen.findByText('Studio Rossi & Associati')).toBeInTheDocument()
    expect(screen.getByText(/Confermo la giornata di giovedì 5 novembre/)).toBeInTheDocument()
    expect(screen.getByText(/Confidenza alta \(93%\)/)).toBeInTheDocument()
    expect(screen.getByText(/Confidenza media \(78%\)/)).toBeInTheDocument()
    const rows = screen.getAllByRole('link')
    expect(rows[0]).toHaveAttribute('href', '/app/proposals/p1')
    expect(rows[1]).toHaveAttribute('href', '/app/proposals/p2')
  })

  it('counts the queue on its own tab', async () => {
    answering({ pending: PENDING })
    renderWithQuery(<ProposalsPage status="pending" onStatusChange={vi.fn()} />)
    expect(await screen.findByRole('tab', { name: /In attesa\s*2/ })).toBeInTheDocument()
  })

  it('says the queue is empty in words, not with a bare table', async () => {
    answering({})
    renderWithQuery(<ProposalsPage status="pending" onStatusChange={vi.fn()} />)
    expect(await screen.findByRole('heading', { name: 'Niente da rivedere' })).toBeInTheDocument()
  })

  it('says who decided and when on a decided tab, and asks the API for the newest decision first', async () => {
    answering({
      rejected: [
        proposal({ id: 'new', status: 'rejected', decided_by: 'Lorenzo Fiore', decided_at: '2026-10-25T10:00:00Z' }),
        proposal({ id: 'old', status: 'rejected', decided_by: 'Ada', decided_at: '2026-10-20T10:00:00Z' }),
      ],
    })
    renderWithQuery(<ProposalsPage status="rejected" onStatusChange={vi.fn()} />)
    const links = await screen.findAllByRole('link')
    // Drawn in the order the API answered: the page does not sort a list it asked to be ordered.
    expect(links.map((link) => link.getAttribute('href'))).toEqual(['/app/proposals/new', '/app/proposals/old'])
    expect(screen.getByText(/Rifiutata da Lorenzo Fiore · 25 ott 2026/)).toBeInTheDocument()
    expect(api.GET).toHaveBeenCalledWith('/api/proposals', {
      params: { query: { status: 'rejected', order: 'decision', limit: 200 } },
    })
    // The queue itself is read in the order things arrived.
    expect(api.GET).toHaveBeenCalledWith('/api/proposals', {
      params: { query: { status: 'pending', order: 'arrival', limit: 200 } },
    })
  })

  it('does not print a placeholder for a decision whose author is not recorded', async () => {
    answering({ accepted: [proposal({ status: 'accepted', decided_by: null, decided_at: '2026-10-25T10:00:00Z' })] })
    renderWithQuery(<ProposalsPage status="accepted" onStatusChange={vi.fn()} />)
    expect(await screen.findByText(/^Accettata · 25 ott 2026/)).toBeInTheDocument()
  })

  it('asks for the clicked tab', async () => {
    answering({ pending: PENDING })
    const onStatusChange = vi.fn()
    renderWithQuery(<ProposalsPage status="pending" onStatusChange={onStatusChange} />)
    await userEvent.click(await screen.findByRole('tab', { name: 'Accettate' }))
    expect(onStatusChange).toHaveBeenCalledWith('accepted')
  })

  it('shows a failed read as a failure, never as an empty queue', async () => {
    answering({}, ['pending'])
    renderWithQuery(<ProposalsPage status="pending" onStatusChange={vi.fn()} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Il database non risponde.')
    await waitFor(() => expect(screen.queryByText('Niente da rivedere')).toBeNull())
  })
})
