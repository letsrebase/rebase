import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdminAgents } from './Agents'

const ACTIVE = {
  id: 't1',
  name: 'Claude Code',
  prefix: 'reb_abcdefgh',
  created_at: '2026-09-15T10:00:00Z',
  last_used_at: null,
  revoked_at: null,
}
const REVOKED = { ...ACTIVE, id: 't0', name: 'Vecchio', prefix: 'reb_00000000', revoked_at: '2026-09-14T10:00:00Z' }

function page(items: unknown[], next_cursor: string | null = null) {
  return { items, next_cursor }
}

function answer(status: number, body: unknown) {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <AdminAgents />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('the Agenti page (REB-213)', () => {
  it('shows the endpoint, the snippets with a placeholder, and the tokens with a revoke on the active one', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => answer(200, page([ACTIVE, REVOKED])))
    mount()
    await screen.findByText('reb_abcdefgh…')
    expect(screen.getByLabelText('Endpoint')).toHaveValue(`${window.location.origin}/api/hub/mcp`)
    expect((screen.getByLabelText('Comando per Claude Code') as HTMLTextAreaElement).value).toContain('Bearer <token>')
    expect(screen.getByText('Revocato')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /^Revoca/ })).toHaveLength(1)
  })

  it('mints a token once, fills the snippets with it, and refreshes the list', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(answer(200, page([])))
    spy.mockResolvedValueOnce(answer(201, { ...ACTIVE, token: 'reb_abcdefgh12345' }))
    spy.mockResolvedValueOnce(answer(200, page([ACTIVE])))
    mount()
    await screen.findByText('Nessun token ancora.')

    const user = userEvent.setup()
    await user.clear(screen.getByLabelText('Nome del token'))
    await user.type(screen.getByLabelText('Nome del token'), 'Claude Code')
    await user.click(screen.getByRole('button', { name: 'Crea il token' }))

    expect(await screen.findByLabelText('Token «Claude Code»')).toHaveValue('reb_abcdefgh12345')
    expect((screen.getByLabelText('Comando per Claude Code') as HTMLTextAreaElement).value).toContain('Bearer reb_abcdefgh12345')
    expect(screen.queryByRole('button', { name: 'Crea il token' })).toBeNull()
    await screen.findByText('reb_abcdefgh…')
    const [createPath, createInit] = spy.mock.calls[1] ?? []
    expect(createPath).toBe('/api/hub/tokens')
    expect(JSON.parse(String((createInit as RequestInit).body))).toEqual({ name: 'Claude Code' })
  })

  it('revokes a token and shows it revoked', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(answer(200, page([ACTIVE])))
    spy.mockResolvedValueOnce(answer(204, undefined))
    spy.mockResolvedValueOnce(answer(200, page([{ ...ACTIVE, revoked_at: '2026-09-15T11:00:00Z' }])))
    mount()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /^Revoca/ }))
    await waitFor(() => expect(screen.getByText('Revocato')).toBeInTheDocument())
    const [path, init] = spy.mock.calls[1] ?? []
    expect(path).toBe('/api/hub/tokens/t1')
    expect((init as RequestInit).method).toBe('DELETE')
  })

  it('says so when a token cannot be revoked, and leaves it active', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(answer(200, page([ACTIVE])))
    spy.mockResolvedValueOnce(answer(500, { detail: 'Riprova tra poco.' }))
    mount()
    await userEvent.setup().click(await screen.findByRole('button', { name: /^Revoca/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Riprova tra poco.')
    expect(screen.getByText('Attivo')).toBeInTheDocument()
  })

  it('debounces the search box and asks the server to narrow the list by name', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(answer(200, page([ACTIVE, REVOKED])))
    spy.mockResolvedValueOnce(answer(200, page([ACTIVE])))
    mount()
    await screen.findByText('Vecchio')
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Cerca token'), 'Claude')
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2), { timeout: 2000 })
    expect(spy.mock.calls[1]![0]).toBe('/api/hub/tokens?q=Claude')
    await waitFor(() => expect(screen.queryByText('Vecchio')).toBeNull())
    expect(screen.getByText('Claude Code', { selector: 'td p' })).toBeInTheDocument()
  })

  it('loads the next page on demand', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(answer(200, page([ACTIVE], 'cursor-1')))
    spy.mockResolvedValueOnce(answer(200, page([REVOKED])))
    mount()
    await screen.findByText('Claude Code', { selector: 'td p' })
    expect(screen.getByText('Mostrati 1 token, ce ne sono altri.')).toBeInTheDocument()

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Mostra altri' }))
    await screen.findByText('Vecchio')
    expect(spy.mock.calls[1]![0]).toBe('/api/hub/tokens?cursor=cursor-1')
    expect(screen.queryByRole('button', { name: 'Mostra altri' })).toBeNull()
  })
})
