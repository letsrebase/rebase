import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { FiscalPanel } from './FiscalPanel'

// See `features/analytics/EconomicsTab.test.tsx` for why the module is mocked rather
// than the network.
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})

// `<Link>` needs a router; this panel only ever renders one, pointing at the settings
// screen that owns the three parameters. Mirrors `AppShell.test.tsx`'s own stub.
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => (
    <a href={to}>{children}</a>
  ),
}))

const AVVERTENZA =
  'Stima indicativa. Non tiene conto del minimale e del massimale contributivo, di altri redditi, degli acconti già versati né di deduzioni e detrazioni. Per la dichiarazione fai riferimento al tuo commercialista.'

const estimate = (overrides: Record<string, unknown> = {}) => ({
  anno: 2026,
  stima: true,
  avvertenza: AVVERTENZA,
  ricavi: '100000.00',
  coefficiente_redditivita: '67.00',
  imponibile: '67000.00',
  aliquota_imposta_sostitutiva: '5.00',
  imposta_sostitutiva: '3350.00',
  aliquota_inps: '26.07',
  contributi: '16593.56',
  reddito_netto_stimato: '80056.44',
  ...overrides,
})

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}

function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) }) as never
}

function renderPanel(anno = 2026) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <FiscalPanel anno={anno} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
})

describe('FiscalPanel', () => {
  it('puts the word "stima" at the top of the page, not at the bottom', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(estimate()))
    const { container } = renderPanel()

    const warning = await screen.findByRole('note')
    const figures = await screen.findByText('80.056,44 €')
    // Asserted by document order, because "at the top" is the requirement and a footnote
    // satisfies the word without the point: somebody reads the net figure, decides
    // something, and never scrolls to the caveat.
    expect(
      warning.compareDocumentPosition(figures) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    expect(warning).toHaveTextContent(/stima/i)
    expect(container.textContent).toMatch(/minimale/)
  })

  it('prints the whole warning the server sent, not a summary of it', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(estimate()))
    renderPanel()

    // The sentence is the backend's, verbatim: which caveats apply is a fiscal question,
    // not a copywriting one, and shortening it here would drop whichever one matters.
    expect(await screen.findByRole('note')).toHaveTextContent(AVVERTENZA)
  })

  it('shows every computed line, rates included', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(estimate()))
    renderPanel()

    expect(await screen.findByText('100.000,00 €')).toBeInTheDocument()
    expect(screen.getByText('67,00 %')).toBeInTheDocument()
    expect(screen.getByText('67.000,00 €')).toBeInTheDocument()
    expect(screen.getByText('5,00 %')).toBeInTheDocument()
    expect(screen.getByText('3.350,00 €')).toBeInTheDocument()
    expect(screen.getByText('26,07 %')).toBeInTheDocument()
    expect(screen.getByText('16.593,56 €')).toBeInTheDocument()
    expect(screen.queryByText(/non calcolabile/i)).not.toBeInTheDocument()
  })

  it('shows a line as not computable when its parameter is missing', async () => {
    vi.mocked(api.GET).mockImplementation(() =>
      ok(
        estimate({
          avvertenza: 'Stima indicativa.',
          ricavi: '1000.00',
          coefficiente_redditivita: null,
          imponibile: null,
          aliquota_imposta_sostitutiva: null,
          imposta_sostitutiva: null,
          aliquota_inps: null,
          contributi: null,
          reddito_netto_stimato: null,
        }),
      ),
    )
    renderPanel()

    // Never zero. An estimated tax of `0,00 €` reads as "you owe nothing", which is a
    // claim this screen is in no position to make about a coefficient nobody has set.
    expect(await screen.findAllByText(/non calcolabile/i)).not.toHaveLength(0)
    expect(screen.queryByText('0,00 €')).not.toBeInTheDocument()
    expect(screen.getByText(/imposta i parametri fiscali/i)).toBeInTheDocument()
    // The revenue is known even when nothing can be derived from it.
    expect(screen.getByText('1.000,00 €')).toBeInTheDocument()
  })

  it('does not offer the settings link when every parameter is set', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(estimate()))
    renderPanel()

    await screen.findByText('80.056,44 €')
    expect(screen.queryByText(/imposta i parametri fiscali/i)).not.toBeInTheDocument()
  })

  /** `get_fiscal_estimate` raises `NotFound("fiscal_profile", "singleton")` when nothing
   *  is configured, whose detail is "fiscal_profile singleton not found" -- true, English
   *  and useless to the person who has to fix it. The panel says which screen instead. */
  it('reads a 404 as a missing fiscal profile and points at the screen that owns it', async () => {
    vi.mocked(api.GET).mockImplementation(() =>
      failed({ detail: 'fiscal_profile singleton not found', code: 'not_found' }, 404),
    )
    renderPanel()

    expect(await screen.findByText(/profilo fiscale non configurato/i)).toBeInTheDocument()
    expect(screen.getByText(/imposta i parametri fiscali/i)).toBeInTheDocument()
    expect(screen.queryByText('fiscal_profile singleton not found')).not.toBeInTheDocument()
  })

  /** The estimate is `admin`-only in the service and the tab is deliberately not hidden:
   *  a non-admin gets the server's explanation rather than a feature that is not there. */
  it('shows a refusal as the server worded it, with no figures under it', async () => {
    vi.mocked(api.GET).mockImplementation(() =>
      failed(
        {
          detail: 'get_fiscal_estimate requires one of [admin], actor has collaboratore',
          code: 'permission_denied',
        },
        403,
      ),
    )
    renderPanel()

    expect(await screen.findByRole('alert')).toHaveTextContent(/requires one of \[admin\]/)
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
    expect(screen.queryByText(/reddito netto/i)).not.toBeInTheDocument()
  })

  it('asks for the year it was given, never for the one on the clock', async () => {
    // The year is the caller's, since this card lives in Home under a period picker: the
    // estimate a reader is looking at has to be the one the period on screen names, and a
    // panel that read the clock instead would answer a question nobody asked while the
    // cards above it answered another.
    vi.mocked(api.GET).mockImplementation(() => ok(estimate({ anno: 2025 })))
    renderPanel(2025)

    await screen.findByRole('note')
    const [path, options] = vi.mocked(api.GET).mock.calls[0] as unknown as [
      string,
      { params: { query: { anno: number } } },
    ]
    expect(path).toBe('/api/analytics/fiscal')
    expect(options.params.query.anno).toBe(2025)
  })
})
