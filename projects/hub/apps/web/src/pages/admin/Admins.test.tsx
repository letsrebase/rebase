import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdminAdmins } from './Admins'

const IVAN = { id: '1', email: 'ivan@rebase.it', nome: 'Ivan', active: true, created_at: '2026-09-10T10:00:00Z' }
const ADA = { id: '2', email: 'ada@rebase.it', nome: 'Ada', active: true, created_at: '2026-09-10T11:00:00Z' }

function page(items: unknown[], next_cursor: string | null = null) {
  return { items, next_cursor }
}

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <AdminAdmins />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('the Amministratori page (REB-279: promote/demote, no password anywhere)', () => {
  it('shows the list and the form on the page, no dialog, no password field', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => answer(200, page([IVAN])))
    mount()
    await screen.findByText('ivan@rebase.it')
    expect(screen.getByLabelText('Email')).toBeInTheDocument()
    expect(screen.getByLabelText('Nome')).toBeInTheDocument()
    expect(screen.getByLabelText('Cognome')).toBeInTheDocument()
    expect(screen.queryByLabelText(/password/i)).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('promotes an address with one click, posting the email alone when nome/cognome are blank', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(answer(200, page([IVAN])))
    spy.mockResolvedValueOnce(answer(200, ADA))
    spy.mockResolvedValueOnce(answer(200, page([IVAN, ADA])))
    mount()
    await screen.findByText('ivan@rebase.it')
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Email'), 'ada@rebase.it')
    await user.click(screen.getByRole('button', { name: 'Promuovi' }))

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Amministratore promosso'))
    expect(screen.getByRole('status')).toHaveTextContent('ada@rebase.it')
    await screen.findByText('ada@rebase.it', { selector: 'td p' })
    const [url, init] = spy.mock.calls[1]!
    expect(url).toBe('/api/hub/admins/promote')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({ email: 'ada@rebase.it' })
    // The form is blank again after a successful promotion.
    expect(screen.getByLabelText('Email')).toHaveValue('')
  })

  it('sends nome/cognome too when typed, for a brand-new address', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(answer(200, page([])))
    spy.mockResolvedValueOnce(answer(201, ADA))
    spy.mockResolvedValueOnce(answer(200, page([ADA])))
    mount()
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1))
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Email'), 'ada@rebase.it')
    await user.type(screen.getByLabelText('Nome'), 'Ada')
    await user.type(screen.getByLabelText('Cognome'), 'Lovelace')
    await user.click(screen.getByRole('button', { name: 'Promuovi' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('ada@rebase.it'))
    const [, init] = spy.mock.calls[1]!
    expect(JSON.parse(init?.body as string)).toEqual({ email: 'ada@rebase.it', nome: 'Ada', last_name: 'Lovelace' })
  })

  it('keeps a refused promotion on the field the server names', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(answer(200, page([IVAN])))
    spy.mockResolvedValueOnce(
      answer(422, { detail: [{ loc: ['body', 'email'], msg: 'indirizzo già usato da un altro account' }] }),
    )
    mount()
    await screen.findByText('ivan@rebase.it')
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Email'), 'occupato@rebase.it')
    await user.click(screen.getByRole('button', { name: 'Promuovi' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('indirizzo già usato da un altro account')
    expect(screen.getByLabelText('Email')).toHaveAttribute('aria-invalid', 'true')
    // The draft stays, so the person corrects the one field rather than retyping.
    expect(screen.getByLabelText('Email')).toHaveValue('occupato@rebase.it')
  })

  it('demotes a row on the click, no confirmation dialog, fully reversible', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(answer(200, page([IVAN, ADA])))
    spy.mockResolvedValueOnce(answer(200, ADA))
    spy.mockResolvedValueOnce(answer(200, page([IVAN])))
    mount()
    await screen.findByText('ada@rebase.it')
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /Rimuovi.*Ada.*ada@rebase\.it/s }))
    expect(screen.queryByRole('dialog')).toBeNull()
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Non è più amministratore'))
    expect(screen.getByRole('status')).toHaveTextContent('ada@rebase.it')
    const [url, init] = spy.mock.calls[1]!
    expect(url).toBe('/api/hub/admins/2/demote')
    expect(init?.method).toBe('POST')
  })

  it('says so under the form when a row cannot be removed, and keeps the row', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(answer(200, page([IVAN, ADA])))
    spy.mockResolvedValueOnce(answer(409, { detail: 'Deve restare almeno un amministratore.' }))
    mount()
    await screen.findByText('ada@rebase.it')
    await userEvent.setup().click(screen.getByRole('button', { name: /Rimuovi.*Ada.*ada@rebase\.it/s }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Deve restare almeno un amministratore.')
    expect(screen.getByText('ada@rebase.it')).toBeInTheDocument()
  })

  it('debounces the search box and asks the server, not the browser, to narrow the list', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(answer(200, page([IVAN, ADA])))
    spy.mockResolvedValueOnce(answer(200, page([ADA])))
    mount()
    await screen.findByText('ivan@rebase.it')
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Cerca amministratori'), 'Ada')
    // No request fires per keystroke; one fires once typing settles.
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2), { timeout: 2000 })
    expect(spy.mock.calls[1]![0]).toBe('/api/hub/admins?q=Ada')
    await waitFor(() => expect(screen.queryByText('ivan@rebase.it')).toBeNull())
    expect(screen.getByText('ada@rebase.it')).toBeInTheDocument()
  })

  it('loads the next page on demand, appending rows rather than replacing them', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(answer(200, page([IVAN], 'cursor-1')))
    spy.mockResolvedValueOnce(answer(200, page([ADA])))
    mount()
    await screen.findByText('ivan@rebase.it')
    expect(screen.getByText(/Mostrati 1 amministratori, ce ne sono altri\./)).toBeInTheDocument()

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Mostra altri' }))
    await screen.findByText('ada@rebase.it')
    expect(screen.getByText('ivan@rebase.it')).toBeInTheDocument()
    expect(spy.mock.calls[1]![0]).toBe('/api/hub/admins?cursor=cursor-1')
    expect(screen.queryByRole('button', { name: 'Mostra altri' })).toBeNull()
  })
})
