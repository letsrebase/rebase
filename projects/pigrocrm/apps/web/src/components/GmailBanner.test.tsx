import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { GmailBanner } from './GmailBanner'
import type { GmailHealth } from '@/features/gmail/queries'
import { api } from '@/lib/api'

// The canonical shape for this codebase: `api` is an openapi-fetch client built at
// import time, so stubbing `globalThis.fetch` would never be seen by it.
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})

// The same substitution AppShell.test.tsx uses: a real `<Link>` needs a router in
// context, and this component is asserted on as a plain component. `to` becomes `href`
// because that is what the router renders and what the assertion below is about.
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children, ...props }: { to: string; children: React.ReactNode }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}))

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

function failed(error: unknown, status: number) {
  return { error, response: new Response(null, { status }) } as never
}

const HEALTHY: GmailHealth = {
  account: null,
  banner: null,
  banner_text: null,
  missing_scopes: [],
  configured: true,
}

function renderBanner() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const utils = render(
    <QueryClientProvider client={client}>
      <GmailBanner />
    </QueryClientProvider>,
  )
  return { ...utils, client }
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
})

describe('GmailBanner', () => {
  it('renders nothing when there is nothing wrong', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(HEALTHY))
    const { container } = renderBanner()

    await waitFor(() => expect(api.GET).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('is a persistent region, not a toast', async () => {
    // A toast that scrolls away is a silent failure with extra steps: nothing here
    // dismisses the banner, because nothing the user can press makes a revoked consent
    // stop being revoked.
    vi.mocked(api.GET).mockResolvedValue(
      ok({
        ...HEALTHY,
        banner: 'revoked',
        banner_text: 'Il consenso Google per ada@acme.it è stato revocato.',
      }),
    )
    renderBanner()

    const banner = await screen.findByRole('alert')
    expect(banner).toHaveTextContent('è stato revocato')
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('carries the backend text verbatim and links to the settings page', async () => {
    // The text comes from the API. Recomputing it here is how the three interfaces
    // start disagreeing.
    vi.mocked(api.GET).mockResolvedValue(
      ok({
        ...HEALTHY,
        banner: 'expiring',
        banner_text: 'Il consenso Google va rinnovato entro il 22/08/2026 alle 10:00.',
      }),
    )
    renderBanner()

    expect(await screen.findByRole('alert')).toHaveTextContent('entro il 22/08/2026 alle 10:00')
    expect(screen.getByRole('link', { name: /Impostazioni/ })).toHaveAttribute(
      'href',
      '/app/settings/gmail',
    )
  })

  /**
   * Three causes, three texts, and none of them invented here: the API decides which
   * one applies (revoked beats expired beats expiring beats a missing read scope) and
   * the banner renders whichever sentence came back.
   */
  it.each([
    ['revoked', 'Il consenso Google per ada@acme.it è stato revocato: ricollega la casella.'],
    ['expired', 'Il consenso Google per ada@acme.it è scaduto e va rinnovato.'],
    ['scope_missing', "Il sync è spento: manca l'autorizzazione gmail.readonly."],
  ])('renders the %s text the API sent, and no wording of its own', async (banner, text) => {
    vi.mocked(api.GET).mockResolvedValue(ok({ ...HEALTHY, banner, banner_text: text }))
    renderBanner()

    expect(await screen.findByRole('alert')).toHaveTextContent(text)
  })

  /**
   * The defect `CostCategoriesPanel` and `RatesPanel` were both fixed for, in the shape
   * that gets it wrong most easily: a persistent banner whose cause has gone away. It
   * is derived from the query on every render and held in no state of its own, so the
   * next health response is the only thing that decides whether it is on screen.
   */
  it('disappears the moment its cause does', async () => {
    vi.mocked(api.GET).mockResolvedValue(
      ok({
        ...HEALTHY,
        banner: 'revoked',
        banner_text: 'Il consenso Google per ada@acme.it è stato revocato.',
      }),
    )
    const { client } = renderBanner()
    expect(await screen.findByRole('alert')).toBeInTheDocument()

    // What re-authorising does: the same query, answered by a healthy account.
    vi.mocked(api.GET).mockResolvedValue(ok(HEALTHY))
    await client.invalidateQueries({ queryKey: ['gmail', 'health'] })

    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  /**
   * The shell banner is not the place to report that the shell banner could not load:
   * it would put a red bar on every page of an otherwise working application during a
   * blip. Impostazioni → Gmail is where a failed read of the Gmail state is reported.
   */
  it('never renders an error of its own when the health request fails', async () => {
    vi.mocked(api.GET).mockResolvedValue(failed({ detail: 'database non raggiungibile' }, 503))
    const { container } = renderBanner()

    await waitFor(() => expect(api.GET).toHaveBeenCalled())
    await waitFor(() => expect(container).toBeEmptyDOMElement())
    expect(screen.queryByText(/database non raggiungibile/)).not.toBeInTheDocument()
  })
})
