import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TeamRisposta } from './TeamRisposta'

function mount(path: string) {
  const root = createRootRoute({ component: () => <Outlet /> })
  const page = createRoute({ getParentRoute: () => root, path: '/team/risposta', component: TeamRisposta })
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: [path] }) })
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

function answering(status: number, body: unknown) {
  return vi
    .spyOn(globalThis, 'fetch')
    .mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
}

const INVALID = 'Questo link non è più valido'

afterEach(() => vi.restoreAllMocks())

describe('the answer to the availability mail (REB-517, spec § 3.2)', () => {
  it('asks before recording, then posts the token and the answer and thanks for the yes', async () => {
    const fetch = answering(200, { esito: 'si' })
    mount('/team/risposta?t=abc_-1&r=si')

    expect(await screen.findByRole('heading', { name: 'Vuoi confermare che sei disponibile?' })).toBeInTheDocument()
    // Opening the link records nothing: a mail scanner opens every link on its own.
    expect(fetch).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Conferma' }))

    expect(
      await screen.findByRole('heading', { name: 'Grazie, abbiamo registrato la tua disponibilità' }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Conferma' })).toBeNull()
    const [url, init] = fetch.mock.calls[0]!
    expect(url).toBe('/api/hub/team/availability')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({ t: 'abc_-1', risposta: 'si' })
  })

  it('asks and thanks for the no in its own words', async () => {
    const fetch = answering(200, { esito: 'no' })
    mount('/team/risposta?t=abc&r=no')

    expect(await screen.findByRole('heading', { name: 'Vuoi confermare che non sei disponibile?' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Conferma' }))

    expect(
      await screen.findByRole('heading', { name: 'Grazie, abbiamo registrato che non sei disponibile' }),
    ).toBeInTheDocument()
    expect(JSON.parse(fetch.mock.calls[0]![1]?.body as string)).toEqual({ t: 'abc', risposta: 'no' })
  })

  it('says the link is no longer valid for a spent, unknown or expired token, and nothing else', async () => {
    answering(200, { esito: 'invalid' })
    mount('/team/risposta?t=abc&r=si')

    await userEvent.click(await screen.findByRole('button', { name: 'Conferma' }))

    expect(await screen.findByRole('heading', { name: INVALID })).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('says the same of a link with no token or no answer, and posts nothing', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch')
    mount('/team/risposta?r=si')
    expect(await screen.findByRole('heading', { name: INVALID })).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
    expect(fetch).not.toHaveBeenCalled()
  })

  it('says the same of an answer that is neither yes nor no', async () => {
    mount('/team/risposta?t=abc&r=forse')
    expect(await screen.findByRole('heading', { name: INVALID })).toBeInTheDocument()
  })

  it('keeps the button when the post fails, with the API’s sentence', async () => {
    answering(429, { detail: 'Troppe richieste da qui. Riprova tra un minuto.' })
    mount('/team/risposta?t=abc&r=si')

    await userEvent.click(await screen.findByRole('button', { name: 'Conferma' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Troppe richieste da qui. Riprova tra un minuto.')
    expect(screen.getByRole('button', { name: 'Conferma' })).toBeEnabled()
    expect(screen.getByRole('heading', { name: 'Vuoi confermare che sei disponibile?' })).toBeInTheDocument()
  })
})
