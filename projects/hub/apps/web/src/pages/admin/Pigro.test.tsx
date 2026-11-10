import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdminPigro } from './Pigro'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, params, children, ...rest }: { to: string; params?: Record<string, string>; children: React.ReactNode }) => (
    <a href={params ? to.replace('$id', params.id!) : to} {...rest}>{children}</a>
  ),
}))

const ADA = {
  slug: 'studio-ada',
  owner_email: 'ada@studio.it',
  created_at: '2026-09-10T09:00:00Z',
  url: 'https://pigro.letsrebase.com/studio-ada/app/',
  member: { id: 'f-1', first_name: 'Ada', last_name: 'Lovelace' },
}
const BOB = {
  slug: 'bob-dev',
  owner_email: 'bob@example.org',
  created_at: '2026-09-09T09:00:00Z',
  url: 'https://pigro.letsrebase.com/bob-dev/app/',
  member: null,
}

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <AdminPigro />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('the Istanze Pigro page', () => {
  it('shows a space whose address is not a web address as its name alone, never as a link', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { total: 1, items: [{ ...ADA, url: 'javascript:alert(1)' }], next_cursor: null }),
    )
    mount()
    expect(await screen.findByText('studio-ada')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'studio-ada' })).toBeNull()
  })

  it('lists every space with a link to it, and names the member who owns one', async () => {
    // ORB-142: which spaces exist and whose they are.
    const spy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(answer(200, { total: 2, items: [ADA, BOB], next_cursor: null }))
    mount()
    const space = await screen.findByRole('link', { name: 'studio-ada' })
    expect(space).toHaveAttribute('href', 'https://pigro.letsrebase.com/studio-ada/app/')
    expect(spy.mock.calls[0]![0]).toBe('/api/hub/pigro/instances')
    // Ada is a member: her name links to her card, and her address is beside it.
    expect(screen.getByRole('link', { name: 'Ada Lovelace' })).toHaveAttribute('href', '/admin/freelance/f-1')
    expect(screen.getByText('ada@studio.it')).toBeInTheDocument()
    // Bob is not: the address is all there is, and nothing claims otherwise.
    expect(screen.getByText('bob@example.org')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /bob/i })).toHaveAttribute('href', 'https://pigro.letsrebase.com/bob-dev/app/')
    expect(screen.getByRole('heading', { name: /Istanze Pigro/ })).toHaveTextContent('2')
  })

  it('says the registry is not configured when the API answers 503 with its sentence', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(503, { detail: 'Il registro di Pigro non è configurato: manca REBASE_PIGRO_REGISTRY_TOKEN.' }),
    )
    mount()
    await screen.findByText('Il registro di Pigro non è configurato: manca REBASE_PIGRO_REGISTRY_TOKEN.')
  })

  it('says when there is nothing yet', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { total: 0, items: [], next_cursor: null }))
    mount()
    await screen.findByText('Nessuna istanza ancora.')
  })

  it('debounces the search box and asks the server, not the browser, to narrow the list (REB-313)', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(answer(200, { total: 2, items: [ADA, BOB], next_cursor: null }))
    spy.mockResolvedValueOnce(answer(200, { total: 1, items: [BOB], next_cursor: null }))
    mount()
    await screen.findByRole('link', { name: 'studio-ada' })
    // One burst (`delay: null`), so the 300 ms debounce cannot expire between two keystrokes
    // however slow the machine is, and the wait is the default, not a fixed 2 s (REB-1029).
    const user = userEvent.setup({ delay: null })
    await user.type(screen.getByLabelText('Cerca istanze'), 'bob@example')
    // No request fires per keystroke; one fires once typing settles.
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2))
    expect(spy.mock.calls[1]![0]).toBe('/api/hub/pigro/instances?q=bob%40example')
    await waitFor(() => expect(screen.queryByRole('link', { name: 'studio-ada' })).toBeNull())
    expect(screen.getByText('bob@example.org')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Istanze Pigro/ })).toHaveTextContent('1')
  })

  it('loads the next page on demand, appending rows rather than replacing them', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(answer(200, { total: 2, items: [ADA], next_cursor: 'cursor-1' }))
    spy.mockResolvedValueOnce(answer(200, { total: 2, items: [BOB], next_cursor: null }))
    mount()
    await screen.findByRole('link', { name: 'studio-ada' })
    expect(screen.getByText('Mostrate 1 istanze, ce ne sono altre.')).toBeInTheDocument()

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Mostra altri' }))
    expect(await screen.findByRole('link', { name: 'bob-dev' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'studio-ada' })).toBeInTheDocument()
    expect(spy.mock.calls[1]![0]).toBe('/api/hub/pigro/instances?cursor=cursor-1')
    expect(screen.queryByRole('button', { name: 'Mostra altri' })).toBeNull()
  })
})
