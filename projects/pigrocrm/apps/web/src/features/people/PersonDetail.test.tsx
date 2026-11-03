import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import type { ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PersonDetail } from './PersonDetail'
import { api } from '@/lib/api'

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return { ...actual, useParams: () => ({ personId: 'p1' }), useNavigate: () => vi.fn() }
})

vi.mock('@/lib/auth', () => ({ useCanWrite: () => false, useCan: () => false }))

// `api.GET` is spied on directly, mirroring `customers/$customerId.test.tsx`: what
// is under test is this route's own handling of what the real `unwrap` produces,
// not a reimplementation of it. `PersonDetail` also mounts `useEntitySchema`
// unconditionally -- that path resolves to a harmless empty success below, since
// it is never read before the isError/404 guard under test here returns.
const mockGet = vi.spyOn(api, 'GET')

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}
function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) }) as never
}

function mockPersonFetch(result: ReturnType<typeof ok> | ReturnType<typeof failed>) {
  mockGet.mockImplementation(
    ((path: string) => (path === '/api/people/{person_id}' ? result : ok({ items: [] }))) as never,
  )
}

beforeEach(() => {
  mockGet.mockReset()
})

const NOTE = {
  id: 'n1',
  entity_type: 'person',
  entity_id: 'p1',
  text: 'Richiamare a settembre.',
  author: 'Giulia Rossi',
  author_id: 'u1',
  source: 'web',
  created_at: '2026-10-01T09:00:00Z',
  edited_at: null,
  edited_by: null,
  editable: true,
} as never

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

describe('PersonDetail', () => {
  it('shows "Persona non trovata" for a genuine 404', async () => {
    mockPersonFetch(
      failed(
        {
          type: 'about:blank',
          title: 'Non trovato',
          status: 404,
          detail: 'persona non trovata',
          code: 'not_found',
        },
        404,
      ),
    )
    renderWithClient(<PersonDetail />)
    expect(await screen.findByText('Persona non trovata.')).toBeInTheDocument()
  })

  /**
   * The bug this guards against: before this fix, `if (!person) return <p>
   * Persona non trovata.</p>` fired for *any* failed fetch, not only a real
   * 404 -- a 500, a 502 or a dropped connection told the user the record does
   * not exist. This reuses `QueryErrorBanner`, the same surface `DataTable` and
   * `Timeline` already show for a failed request everywhere else in this app,
   * rather than inventing a fourth way to say "something went wrong".
   */
  it('shows the failed-request banner, not "Persona non trovata", when the fetch fails for a reason other than 404', async () => {
    mockPersonFetch(failed({ code: 'http_error', detail: 'Il server non risponde.', status: 503 }, 503))
    renderWithClient(<PersonDetail />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Il server non risponde.')
    expect(screen.queryByText('Persona non trovata.')).not.toBeInTheDocument()
  })

  /** REB-879: the Note card of the overview is the person's thread, read from the notes
   *  endpoint and not from a field of the person. A reader sees it and no form. */
  it('shows the person’s notes thread in the overview', async () => {
    mockGet.mockImplementation(
      ((path: string) =>
        path === '/api/people/{person_id}'
          ? ok({ id: 'p1', nome: 'Giulia', last_name: 'Rossi', custom_fields: {} })
          : path === '/api/{collection}/{entity_id}/notes'
            ? ok([NOTE])
            : ok({ items: [] })) as never,
    )
    renderWithClient(<PersonDetail />)
    expect(await screen.findByText('Richiamare a settembre.')).toBeInTheDocument()
    expect(mockGet).toHaveBeenCalledWith('/api/{collection}/{entity_id}/notes', {
      params: { path: { collection: 'people', entity_id: 'p1' } },
    })
    expect(screen.queryByLabelText('Nuova nota')).not.toBeInTheDocument()
  })
})
