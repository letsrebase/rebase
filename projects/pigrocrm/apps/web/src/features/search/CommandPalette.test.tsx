/**
 * §8.6's three states, as three renderings, plus the sub-three-character invitation.
 *
 * The third state is the one that gets forgotten, so it is the one with the sharpest
 * assertion: with the query failing, the string "Nessun risultato" must be **absent from
 * the DOM**. An empty list drawn after an error *is* a wrong answer -- it says "there is
 * none" when the truth is "I do not know".
 *
 * `vi.mock('@/lib/api')` and not a stubbed `globalThis.fetch`: `api` is an openapi-fetch
 * client built at import time, which captured `fetch` before any test could replace it.
 * Real timers, not fake ones: the debounce is 250 ms, and a fake clock plus
 * `userEvent`'s own awaiting is a deadlock this suite has no reason to risk.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { CommandPalette } from './CommandPalette'
import { SEARCH_DEBOUNCE_MS, type SearchResults } from './queries'
import { api } from '@/lib/api'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})

const navigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigate }))

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

function failed(error: unknown, status: number) {
  return { error, response: new Response(null, { status }) } as never
}

const CUSTOMER_ID = '0192f3b2-8c1a-7c3d-9f4e-1a2b3c4d5e6f'

function results(
  overrides: Partial<{ totale: number; totale_e_un_minimo: boolean }> = {},
): SearchResults {
  return {
    termine: 'rossi',
    gruppi: [
      {
        entity: 'customer',
        totale: overrides.totale ?? 1,
        totale_e_un_minimo: overrides.totale_e_un_minimo ?? false,
        hits: [
          {
            entity: 'customer',
            id: CUSTOMER_ID,
            etichetta: 'Rossi Ingegneria Srl',
            sottotitolo: '01234567890',
            punteggio: '0.8000',
            campo: 'ragione_sociale',
          },
        ],
      },
      { entity: 'person', totale: 0, totale_e_un_minimo: false, hits: [] },
      { entity: 'deal', totale: 0, totale_e_un_minimo: false, hits: [] },
      { entity: 'document', totale: 0, totale_e_un_minimo: false, hits: [] },
    ],
  }
}

const onOpenChange = vi.fn()

function renderPalette() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  })
  return render(
    <QueryClientProvider client={client}>
      <CommandPalette open onOpenChange={onOpenChange} />
    </QueryClientProvider>,
  )
}

/** Types the term and waits past the debounce window. */
async function type(term: string) {
  const user = userEvent.setup()
  await user.type(screen.getByRole('combobox'), term)
  return user
}

/** The debounce window plus a margin, in real time. */
function settle() {
  return new Promise((resolve) => setTimeout(resolve, SEARCH_DEBOUNCE_MS + 150))
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  navigate.mockReset()
  onOpenChange.mockReset()
})

describe('CommandPalette', () => {
  it('invites more typing below three characters and issues no request', async () => {
    renderPalette()
    await type('ro')
    await settle()

    expect(screen.getByText(/continua a scrivere/i)).toBeInTheDocument()
    expect(api.GET).not.toHaveBeenCalled()
    expect(screen.queryByText(/nessun risultato/i)).not.toBeInTheDocument()
  })

  it('debounces a burst of typing into a single request for the final term', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(results()))
    renderPalette()
    await type('rossi')
    await settle()

    await waitFor(() => expect(api.GET).toHaveBeenCalledTimes(1))
    // Without the debounce this is three calls -- "ros", "ross", "rossi" -- and the
    // count alone would not say which term actually reached the server.
    expect(api.GET).toHaveBeenCalledWith('/api/search', {
      params: { query: { q: 'rossi' } },
    })
  })

  it('renders a hit with its subtitle', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(results()))
    renderPalette()
    await type('rossi')

    expect(await screen.findByText('Rossi Ingegneria Srl')).toBeInTheDocument()
    expect(screen.getByText('01234567890')).toBeInTheDocument()
  })

  it('opens the record a hit stands for, and closes', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(results()))
    renderPalette()
    const user = await type('rossi')
    await user.click(await screen.findByRole('option', { name: /Rossi Ingegneria Srl/ }))

    expect(navigate).toHaveBeenCalledWith({
      to: '/app/customers/$customerId',
      params: { customerId: CUSTOMER_ID },
    })
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('states the real count and offers "vedi tutti" when the group is truncated', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(results({ totale: 42 })))
    renderPalette()
    await type('rossi')

    expect(await screen.findByText('Clienti · 42')).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /vedi tutti/i })).toBeInTheDocument()
    // One hit plus the "vedi tutti" row, and nothing invented to fill the gap between
    // five shown and forty-two counted.
    expect(screen.getAllByRole('option')).toHaveLength(2)
  })

  it('does not offer "vedi tutti" when the group is whole', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(results({ totale: 1 })))
    renderPalette()
    await type('rossi')

    expect(await screen.findByText('Clienti · 1')).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /vedi tutti/i })).not.toBeInTheDocument()
  })

  it('sends "vedi tutti" to the same list, filtered by the same term', async () => {
    vi.mocked(api.GET).mockResolvedValue(ok(results({ totale: 42 })))
    renderPalette()
    const user = await type('rossi')
    await user.click(await screen.findByRole('option', { name: /vedi tutti/i }))

    // The term travels in the URL, or the list the user lands on is not the list the
    // palette was describing.
    expect(navigate).toHaveBeenCalledWith({
      to: '/app/customers',
      search: { search: 'rossi' },
    })
  })

  it('says "oltre 200" rather than "200" when the count is a minimum', async () => {
    vi.mocked(api.GET).mockResolvedValue(
      ok(results({ totale: 200, totale_e_un_minimo: true })),
    )
    renderPalette()
    await type('rossi')

    expect(await screen.findByText('Clienti · oltre 200')).toBeInTheDocument()
    expect(screen.queryByText('Clienti · 200')).not.toBeInTheDocument()
    expect(screen.getByRole('option', { name: /vedi tutti oltre 200/i })).toBeInTheDocument()
  })

  it('says there is nothing, naming the term, when every group is empty', async () => {
    const empty: SearchResults = {
      termine: 'zzzqqq',
      gruppi: results().gruppi.map((group) => ({ ...group, hits: [], totale: 0 })),
    }
    vi.mocked(api.GET).mockResolvedValue(ok(empty))
    renderPalette()
    await type('zzzqqq')

    expect(await screen.findByText(/nessun risultato per/i)).toHaveTextContent('zzzqqq')
  })

  it('renders the error state and NOT an empty result when the query fails', async () => {
    vi.mocked(api.GET).mockResolvedValue(
      failed(
        {
          type: 'about:blank',
          title: 'Errore',
          status: 500,
          detail: 'Ricerca non disponibile',
          code: 'internal',
        },
        500,
      ),
    )
    renderPalette()
    await type('rossi')

    expect(await screen.findByRole('alert')).toHaveTextContent(/ricerca non disponibile/i)
    // The assertion §8.6 exists for.
    expect(screen.queryByText(/nessun risultato/i)).not.toBeInTheDocument()
    expect(screen.queryAllByRole('option')).toHaveLength(0)
    // And not a spinner either: on a failure `isPending` is false while `data` is still
    // undefined, so a list rendered before the error branch is checked answers a failed
    // search with "Ricerca in corso…" that never resolves.
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('toggles on Cmd/Ctrl+K from anywhere on the page', async () => {
    renderPalette()
    const user = userEvent.setup()
    await user.keyboard('{Control>}k{/Control}')

    // `open` is true in this harness, so the toggle asks to close it: the assertion is
    // that the shortcut is wired to the current state, not that it always opens.
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})
