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
          ? ok({ id: 'p1', first_name: 'Giulia', last_name: 'Rossi', contact_points: [], custom_fields: {} })
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

  /** The overview also draws the notes thread, which is a bare array and not a page. */
  function mockPersonWithNotes(person: object) {
    mockGet.mockImplementation(
      ((path: string) =>
        path === '/api/people/{person_id}'
          ? ok(person)
          : path === '/api/{collection}/{entity_id}/notes'
            ? ok([])
            : ok({ items: [] })) as never,
    )
  }
  it('shows every contact point with its label, note and primary marker, as a link to act on', async () => {
    const points = [
      { id: 'k1', kind: 'email', value: 'giulia@acme.it', label: 'work', note: null, is_primary: true, position: 0 },
      { id: 'k2', kind: 'email', value: 'giulia@casa.it', label: 'personal', note: null, is_primary: false, position: 1 },
      { id: 'k3', kind: 'phone', value: '+39 333 123-4567', label: 'personal', note: 'solo WhatsApp', is_primary: true, position: 2 },
      { id: 'k4', kind: 'linkedin', value: 'https://www.linkedin.com/in/giulia', label: 'work', note: null, is_primary: true, position: 3 },
    ]
    mockPersonWithNotes({
      id: 'p1',
      first_name: 'Giulia',
      last_name: 'Rossi',
      contact_points: points,
      custom_fields: {},
    })
    renderWithClient(<PersonDetail />)

    const work = await screen.findByRole('link', { name: 'giulia@acme.it' })
    expect(work).toHaveAttribute('href', 'mailto:giulia@acme.it')
    expect(screen.getByRole('link', { name: 'giulia@casa.it' })).toHaveAttribute('href', 'mailto:giulia@casa.it')

    const phone = screen.getByRole('link', { name: '+39 333 123-4567' })
    expect(phone).toHaveAttribute('href', 'tel:+393331234567')
    expect(screen.getByText('solo WhatsApp')).toBeInTheDocument()

    const profile = screen.getByRole('link', { name: 'https://www.linkedin.com/in/giulia' })
    expect(profile).toHaveAttribute('href', 'https://www.linkedin.com/in/giulia')
    expect(profile).toHaveAttribute('target', '_blank')
    expect(profile).toHaveAttribute('rel', 'noopener noreferrer')
    expect(work).not.toHaveAttribute('target')

    // One marker per kind that has points: the work address, the number and the profile.
    expect(screen.getAllByText('Principale')).toHaveLength(3)
    expect(screen.getAllByText('personale')).toHaveLength(2)
    expect(work.closest('li')).toHaveTextContent('Principale')
    expect(screen.getByRole('link', { name: 'giulia@casa.it' }).closest('li')).not.toHaveTextContent('Principale')
  })

  it('says so when the person has no contact point', async () => {
    mockPersonWithNotes({ id: 'p1', first_name: 'Giulia', last_name: 'Rossi', contact_points: [], custom_fields: {} })
    renderWithClient(<PersonDetail />)
    expect(await screen.findByText('Nessun recapito.')).toBeInTheDocument()
  })
})
