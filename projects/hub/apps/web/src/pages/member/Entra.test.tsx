import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { render, screen } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { stripEntraToken, takeEntraToken } from '@/lib/entra-token'
import { Entra } from './Entra'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const PROFILE = {
  id: 'f1',
  nome: 'Ada',
  cognome: 'Lovelace',
  email: 'ada@studio.it',
  linkedin_url: null,
  cv_filename: 'cv.pdf',
  cv_size: 1024,
  tariffa_giornaliera: '450.00',
  posizione: 'Backend developer',
  remoto: 'remoto',
  links: [],
  created_at: '2026-09-10T10:00:00Z',
  updated_at: '2026-09-10T10:00:00Z',
}

function mount(path: string, { strict = false }: { strict?: boolean } = {}) {
  const root = createRootRoute({ component: () => <Outlet /> })
  const entra = createRoute({
    getParentRoute: () => root,
    path: '/verify',
    validateSearch: (search: Record<string, unknown>): { t: string } => ({ t: String(search.t ?? '') }),
    component: Entra,
  })
  const me = createRoute({ getParentRoute: () => root, path: '/me', component: () => <h1>La tua area</h1> })
  const login = createRoute({ getParentRoute: () => root, path: '/login', component: () => <h1>Accedi</h1> })
  const router = createRouter({
    routeTree: root.addChildren([entra, me, login]),
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  const tree = (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
  render(strict ? <StrictMode>{tree}</StrictMode> : tree)
}

afterEach(() => {
  vi.restoreAllMocks()
  // Whatever a test left behind in the browser's own URL and in the module's
  // one-shot slot, so the next test's `?? fromSearch` fallback starts clean.
  window.history.replaceState(null, '', '/')
  takeEntraToken()
})

describe('/verify', () => {
  it('posts the token from the URL once and goes to the area', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, PROFILE))
    mount('/verify?t=abc-123_XYZ')
    await screen.findByRole('heading', { name: 'La tua area' })
    const enterCalls = fetchSpy.mock.calls.filter(([url]) => url === '/api/hub/auth/enter')
    expect(enterCalls).toHaveLength(1)
    expect(enterCalls[0]![1]).toMatchObject({ method: 'POST', body: JSON.stringify({ token: 'abc-123_XYZ' }) })
  })

  it('says the link is no longer valid and offers another', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(401, { detail: 'Link non valido o scaduto. Chiedine un altro.' }),
    )
    mount('/verify?t=abc-123_XYZ')
    expect(await screen.findByRole('alert')).toHaveTextContent('non è più valido')
    expect(screen.getByRole('link', { name: /Chiedine un altro/ })).toHaveAttribute('href', '/login')
  })

  it('reaches the area under StrictMode, whose double effect breaks the mutation observer', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, PROFILE))
    mount('/verify?t=abc-123_XYZ', { strict: true })
    await screen.findByRole('heading', { name: 'La tua area' })
  })

  it('posts the token stripEntraToken already took out of the URL, not only the search-param fallback', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, PROFILE))
    window.history.replaceState(null, '', '/hub/verify?t=abc-123_XYZ')
    stripEntraToken()
    // The router mounts at a clean path with no `t`: the only way the token reaches
    // `mutateAsync` is `takeEntraToken()`'s one-shot read, under StrictMode's double
    // invoke of the `useState` initialiser.
    mount('/verify', { strict: true })
    await screen.findByRole('heading', { name: 'La tua area' })
    const enterCalls = fetchSpy.mock.calls.filter(([url]) => url === '/api/hub/auth/enter')
    expect(enterCalls).toHaveLength(1)
    expect(enterCalls[0]![1]).toMatchObject({ method: 'POST', body: JSON.stringify({ token: 'abc-123_XYZ' }) })
  })

  it('shows the API message and a retry on a 429, not the dead-link sentence', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(429, { detail: 'Troppe richieste da qui. Riprova tra un minuto.' }),
    )
    mount('/verify?t=abc-123_XYZ')
    expect(await screen.findByRole('alert')).toHaveTextContent('Troppe richieste da qui. Riprova tra un minuto.')
    expect(screen.getByRole('button', { name: 'Riprova' })).toBeInTheDocument()
    expect(screen.queryByText(/non è più valido/)).not.toBeInTheDocument()
  })
})
