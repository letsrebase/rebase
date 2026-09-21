import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdminGuida } from './Guida'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, params, children, ...rest }: { to: string; params?: Record<string, string>; children: React.ReactNode }) => (
    <a href={params ? to.replace('$id', params.id!) : to} {...rest}>{children}</a>
  ),
}))

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <AdminGuida />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('the La guida page', () => {
  it('shows the three numbers and the latest downloads, each naming the member (ORB-156)', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, {
        totale: 3,
        membri: 1,
        membri_totali: 2,
        ultimi_7_giorni: 3,
        recenti: [
          { id: 'd-1', freelancer_id: 'f-1', nome: 'Ada', cognome: 'Lovelace', email: 'ada@studio.it', downloaded_at: '2026-09-11T09:00:00Z' },
        ],
      }),
    )
    mount()
    const who = await screen.findByRole('link', { name: 'Ada Lovelace' })
    expect(who).toHaveAttribute('href', '/admin/freelance/f-1')
    expect(spy.mock.calls[0]![0]).toBe('/api/hub/perks/guide')
    expect(screen.getByRole('heading', { name: /La guida/ })).toHaveTextContent('3')
    expect(screen.getByText('Membri che l’hanno scaricata').nextElementSibling).toHaveTextContent('1su 2')
    expect(screen.getByText('Ultimi 7 giorni').nextElementSibling).toHaveTextContent('3')
    expect(screen.getByText('ada@studio.it')).toBeInTheDocument()
  })

  it('says when nobody has taken it yet', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { totale: 0, membri: 0, membri_totali: 4, ultimi_7_giorni: 0, recenti: [] }),
    )
    mount()
    await screen.findByText('Nessun download ancora.')
    expect(screen.getByText('Membri che l’hanno scaricata').nextElementSibling).toHaveTextContent('0su 4')
  })

  it('says when the numbers cannot be read', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(500, { detail: 'boom' }))
    mount()
    await screen.findByText('Non riesco a leggere i download.')
  })
})
