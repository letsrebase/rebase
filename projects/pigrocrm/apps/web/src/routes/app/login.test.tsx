/**
 * The login card, email-first (spec 2026-09-12 §6.2): a link by mail is the way in, and
 * the password form is the second door for whoever has one. `useAuth` and the API client
 * are stubbed at the same seams every other route test uses.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const navigate = vi.fn()
let search: { redirect?: string } = {}
vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useNavigate: () => navigate,
    useSearch: () => search,
    createFileRoute: () => (options: unknown) => options,
  }
})

const login = vi.fn()
const user: { value: unknown } = { value: null }
vi.mock('@/lib/auth', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/auth')>()
  return { ...actual, useAuth: () => ({ user: user.value, login }) }
})

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() } }
})

import { api } from '@/lib/api'
import { LoginPage } from './login'

const GET = api.GET as unknown as ReturnType<typeof vi.fn>
const POST = api.POST as unknown as ReturnType<typeof vi.fn>

beforeEach(() => {
  GET.mockReset()
  POST.mockReset()
  login.mockReset()
  navigate.mockReset()
  search = {}
  user.value = null
  // `/api/tenants/root`: this installation has no name.
  GET.mockResolvedValue({ data: { slug: null }, response: { status: 200 } })
})

describe('the post-session redirect', () => {
  beforeEach(() => {
    user.value = { id: 'u1', email: 'ada@studio.it', nome: 'Ada', ruolo: 'admin' }
  })

  it('sends an already-authenticated visitor to the deep link the guard recorded', async () => {
    search = { redirect: '/app/fatture/42' }
    render(<LoginPage />)
    await waitFor(() => expect(navigate).toHaveBeenCalledWith({ href: '/app/fatture/42' }))
  })

  it('ignores a redirect that does not point under this app, falling back to the dashboard', async () => {
    search = { redirect: 'https://evil.example/steal' }
    render(<LoginPage />)
    await waitFor(() => expect(navigate).toHaveBeenCalled())
    expect(navigate).not.toHaveBeenCalledWith({ href: 'https://evil.example/steal' })
    expect(navigate).toHaveBeenCalledWith(expect.objectContaining({ to: '/app' }))
  })
})

describe('the login page', () => {
  it('asks only for the email and sends the link', async () => {
    POST.mockResolvedValue({ data: { ok: true }, response: { status: 202 } })
    render(<LoginPage />)
    expect(screen.queryByLabelText('Password')).toBeNull()
    await userEvent.type(screen.getByLabelText('Email'), 'ada@studio.it')
    await userEvent.click(screen.getByRole('button', { name: 'Mandami il link' }))
    await waitFor(() =>
      expect(POST).toHaveBeenCalledWith('/api/auth/link', { body: { email: 'ada@studio.it' } }),
    )
    expect(await screen.findByRole('status')).toHaveTextContent(/Controlla la posta/)
    expect(login).not.toHaveBeenCalled()
  })

  it('shows the sentence when this installation cannot mail', async () => {
    POST.mockResolvedValue({
      error: { detail: "L'accesso via email non è ancora attivo su questa installazione." },
      response: { status: 503 },
    })
    render(<LoginPage />)
    await userEvent.type(screen.getByLabelText('Email'), 'ada@studio.it')
    await userEvent.click(screen.getByRole('button', { name: 'Mandami il link' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/non è ancora attivo/)
  })

  it('still lets whoever has a password use it', async () => {
    login.mockResolvedValue(undefined)
    render(<LoginPage />)
    await userEvent.click(screen.getByRole('button', { name: /Accedi con la password/ }))
    await userEvent.type(screen.getByLabelText('Email'), 'ada@studio.it')
    await userEvent.type(screen.getByLabelText('Password'), 'lunghissima1')
    await userEvent.click(screen.getByRole('button', { name: 'Accedi' }))
    await waitFor(() => expect(login).toHaveBeenCalledWith('ada@studio.it', 'lunghissima1'))
    expect(POST).not.toHaveBeenCalled()
  })

  it('offers to create a space only on the root', async () => {
    render(<LoginPage />)
    expect(await screen.findByRole('button', { name: 'Crea il tuo spazio' })).toBeInTheDocument()
  })
})
