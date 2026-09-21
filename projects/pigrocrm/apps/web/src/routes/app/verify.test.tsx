/** The page a link by mail lands on (spec 2026-09-12 §6.2). */
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return { ...actual, createFileRoute: () => (options: unknown) => options }
})

const enterWithLink = vi.fn()
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: null, enterWithLink }) }))

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() } }
})

import { api, unwrap } from '@/lib/api'
import { EnterPage } from './verify'

const GET = api.GET as unknown as ReturnType<typeof vi.fn>
const POST = api.POST as unknown as ReturnType<typeof vi.fn>

beforeEach(() => {
  GET.mockReset()
  POST.mockReset()
  enterWithLink.mockReset()
  // `/api/tenants/root`: this installation has no name.
  GET.mockResolvedValue({ data: { slug: null }, response: { status: 200 } })
  // The real `AuthProvider` does exactly this and publishes the user; here the seam is
  // the API client, as in every other route test.
  enterWithLink.mockImplementation(async (t: string) => {
    await unwrap(api.POST('/api/auth/verify', { body: { t } }))
  })
})

describe('the entry page', () => {
  it('spends the token and goes to the home', async () => {
    POST.mockResolvedValue({ data: { id: 'u1', email: 'ada@x.it' }, response: { status: 200 } })
    const go = vi.fn()
    render(<EnterPage token="abc" go={go} />)
    await waitFor(() => expect(enterWithLink).toHaveBeenCalledWith('abc'))
    expect(POST).toHaveBeenCalledWith('/api/auth/verify', { body: { t: 'abc' } })
    await waitFor(() => expect(go).toHaveBeenCalledWith('/app/'))
  })

  it('shows the sentence and the way back when the token is dead', async () => {
    POST.mockResolvedValue({
      error: { detail: 'Questo link non è valido o è scaduto. Chiedine un altro.' },
      response: { status: 401 },
    })
    const go = vi.fn()
    render(<EnterPage token="abc" go={go} />)
    expect(await screen.findByRole('alert')).toHaveTextContent(/non è valido/)
    expect(screen.getByRole('link', { name: /Torna al login/ })).toHaveAttribute(
      'href',
      '/app/login',
    )
    expect(go).not.toHaveBeenCalled()
  })

  it('spends the token once, however often the page re-renders', async () => {
    POST.mockResolvedValue({ data: { id: 'u1', email: 'ada@x.it' }, response: { status: 200 } })
    const { rerender } = render(<EnterPage token="abc" go={vi.fn()} />)
    // A new `go` and a new `enterWithLink` closure, as `AuthProvider` produces on every
    // render: the effect must not run the entry again.
    rerender(<EnterPage token="abc" go={vi.fn()} />)
    await waitFor(() => expect(enterWithLink).toHaveBeenCalledTimes(1))
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(enterWithLink).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('alert')).toBeNull()
  })
})
