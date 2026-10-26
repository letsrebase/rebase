import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/query'
import { PendingProposalsNotice } from './PendingProposalsNotice'
import { ok, proposal, refused, renderWithQuery } from './testing'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => <a href={to}>{children}</a>,
}))

const auth = { canWrite: true }
vi.mock('@/lib/auth', () => ({ useCanWrite: () => auth.canWrite }))

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  auth.canWrite = true
})

describe('the Home notice', () => {
  it('counts what waits and opens the queue', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok({ items: [proposal(), proposal({ id: 'p2' }), proposal({ id: 'p3' })] }) as never)
    renderWithQuery(<PendingProposalsNotice />)
    expect(await screen.findByRole('heading', { name: '3 proposte da rivedere' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Rivedi le proposte/ })).toHaveAttribute('href', '/app/proposals')
    expect(api.GET).toHaveBeenCalledWith('/api/proposals', {
      params: { query: { stato: 'in_attesa', ordine: 'arrivo', limit: 200 } },
    })
  })

  it('speaks in the singular for one', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok({ items: [proposal()] }) as never)
    renderWithQuery(<PendingProposalsNotice />)
    expect(await screen.findByRole('heading', { name: '1 proposta da rivedere' })).toBeInTheDocument()
  })

  it('says «200+» when the queue is longer than one read can see', async () => {
    const full = Array.from({ length: 200 }, (_, index) => proposal({ id: `p${index}` }))
    vi.mocked(api.GET).mockResolvedValue(ok({ items: full }) as never)
    renderWithQuery(<PendingProposalsNotice />)
    expect(await screen.findByRole('heading', { name: '200+ proposte da rivedere' })).toBeInTheDocument()
  })

  it('is not drawn for an empty queue, once the empty answer is in', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok({ items: [] }) as never)
    const client = renderWithQuery(<PendingProposalsNotice />)
    await waitFor(() => expect(client.getQueryState(queryKeys.proposals('in_attesa'))?.status).toBe('success'))
    expect(screen.queryByRole('heading')).toBeNull()
  })

  it('does not even read the queue for someone who cannot decide', async () => {
    auth.canWrite = false
    vi.mocked(api.GET).mockResolvedValue(ok({ items: [proposal()] }) as never)
    renderWithQuery(<PendingProposalsNotice />)
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(api.GET).not.toHaveBeenCalled()
    expect(screen.queryByRole('heading')).toBeNull()
  })

  it('does not claim a count it could not read, once the failure is in', async () => {
    vi.mocked(api.GET).mockResolvedValue(
      refused(500, { title: 'x', status: 500, detail: 'boom', code: 'internal', instance: '/' }) as never,
    )
    const client = renderWithQuery(<PendingProposalsNotice />)
    await waitFor(() => expect(client.getQueryState(queryKeys.proposals('in_attesa'))?.status).toBe('error'))
    expect(screen.queryByRole('heading')).toBeNull()
  })
})
