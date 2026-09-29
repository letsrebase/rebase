/**
 * The Drive page, where `GET /api/drive/oauth/callback` lands every consent outcome
 * (REB-457). A collaboratore now opens it past the settings gate (`SettingsLayout`), so
 * this is the page that reads them the outcome, above their own Drive panel, the same
 * way it always did for an admin. `SettingsLayout.test.tsx` proves the gate lets them in;
 * this proves what they find once in.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import type { ComponentType } from 'react'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

let search: { esito?: string } = {}
vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    // The route reads its search through `Route.useSearch()`, so the stand-in route
    // carries that hook next to the options it was created with.
    createFileRoute: () => (options: object) => ({ options, useSearch: () => search }),
  }
})

const mockAuth = vi.hoisted(() => ({ ruolo: 'collaboratore' as 'admin' | 'collaboratore' }))
vi.mock('@/lib/auth', () => ({
  useIsAdmin: () => mockAuth.ruolo === 'admin',
  useCanWrite: () => true,
  // `useDriveHealth` (`features/drive/queries.ts`) reads `user.ruolo` itself, to key
  // its query by role (REB-562 fix round 2), so the mocked panel underneath this page
  // needs the same `useAuth` every other Drive test file mocks.
  useAuth: () => ({ user: { ruolo: mockAuth.ruolo } }),
}))

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() } }
})

import { api } from '@/lib/api'
import { Route } from './drive'

const GET = api.GET as unknown as ReturnType<typeof vi.fn>

const OUTCOMES = [
  ['collegato', 'Google Drive collegato.'],
  ['negato', 'Autorizzazione negata: Drive non è stato collegato.'],
  ['errore', 'Il collegamento non è andato a buon fine.'],
  ['sessione', 'La sessione è scaduta mentre eri su Google, quindi Drive non è stato collegato.'],
] as const

// The router plugin splits a route's component into its own chunk (`autoCodeSplitting`
// in `vite.config.ts`, which the tests run under too), so `component` is a lazy stand-in
// that suspends on first render. Loading it once up front keeps every render synchronous.
const Page = (Route as unknown as { options: { component: ComponentType & { preload?: () => Promise<void> } } })
  .options.component

// Transforming that chunk is real work, so the hook gets the suite's own test timeout
// rather than Vitest's 10 s hook default, which a loaded machine overran.
beforeAll(async () => {
  await Page.preload?.()
}, 20_000)

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <Page />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  search = {}
  mockAuth.ruolo = 'collaboratore'
  GET.mockReset()
  // Configured, nothing connected: the state a first consent comes back to.
  GET.mockResolvedValue({
    data: { account: null, banner: null, banner_text: null, missing_scopes: [], configured: true },
    response: new Response(null, { status: 200 }),
  })
})

describe('the Drive settings page', () => {
  it.each(OUTCOMES)(
    'reads a collaboratore the %s outcome above their own Drive panel',
    async (esito, sentence) => {
      search = { esito }
      renderPage()
      expect(screen.getByRole('status')).toHaveTextContent(sentence)
      const connect = await screen.findByRole('link', { name: 'Collega Google Drive' })
      expect(
        screen.getByRole('status').compareDocumentPosition(connect) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy()
      if (esito === 'sessione') {
        expect(screen.getByRole('link', { name: 'Riprova' })).toHaveAttribute(
          'href',
          '/api/drive/oauth/start',
        )
      } else {
        expect(screen.queryByRole('link', { name: 'Riprova' })).not.toBeInTheDocument()
      }
    },
  )

  it('reads no outcome when there is none, or one it does not know', async () => {
    const { unmount } = renderPage()
    expect(await screen.findByRole('link', { name: 'Collega Google Drive' })).toBeInTheDocument()
    expect(screen.queryByRole('status')).toBeNull()
    unmount()
    search = { esito: 'Scrivi quello che vuoi' }
    renderPage()
    expect(await screen.findByRole('link', { name: 'Collega Google Drive' })).toBeInTheDocument()
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('reads an admin the outcome as before', async () => {
    mockAuth.ruolo = 'admin'
    search = { esito: 'collegato' }
    renderPage()
    expect(screen.getByRole('status')).toHaveTextContent('Google Drive collegato.')
    expect(await screen.findByRole('link', { name: 'Collega Google Drive' })).toBeInTheDocument()
  })
})
