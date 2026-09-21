import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { OfferStatePicker } from './OfferStatePicker'
import type { Document } from './queries'

const OFFER: Document = {
  id: 'doc-1',
  customer_id: 'c-1',
  deal_id: null,
  tipo: 'offerta',
  titolo: 'Offerta 2026-01',
  stato: 'bozza',
  // Slice 6 §4.1 added the day the current `stato` was set to `DocumentRead`. `null` on
  // an offer still in `bozza`: a draft has no state change to date, and "ferma da N
  // giorni" is not a question anyone asks about one.
  stato_dal: null,
  versione_corrente: 1,
  custom_fields: {},
  created_at: '2026-08-10T09:00:00Z',
  updated_at: '2026-08-10T09:00:00Z',
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

// `api.POST` is spied on directly, not `globalThis.fetch` -- see queries.test.tsx
// and task-14-report.md for why a `globalThis.fetch` mock never intercepts a
// request made through the shared `openapi-fetch` client.
const mockPost = vi.spyOn(api, 'POST')

afterEach(() => {
  mockPost.mockReset()
})

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) } as never)
}

function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) } as never)
}

describe('OfferStatePicker', () => {
  it('offers only the transitions the backend allows from the current state', () => {
    render(<OfferStatePicker document={OFFER} />, { wrapper })
    expect(screen.getByRole('button', { name: 'Segna come Inviata' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Segna come Accettata' })).not.toBeInTheDocument()
  })

  it('offers nothing at all from a terminal state', () => {
    render(<OfferStatePicker document={{ ...OFFER, stato: 'accettata' }} />, { wrapper })
    expect(screen.getByText('Accettata')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Segna come/ })).not.toBeInTheDocument()
  })

  it('renders nothing for a document that is not an offer', () => {
    const { container } = render(
      <OfferStatePicker document={{ ...OFFER, tipo: 'verbale', stato: null }} />,
      { wrapper },
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('posts the chosen state', async () => {
    mockPost.mockReturnValue(ok({ ...OFFER, stato: 'inviata' }))
    render(<OfferStatePicker document={OFFER} />, { wrapper })
    await userEvent.click(screen.getByRole('button', { name: 'Segna come Inviata' }))
    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        '/api/documents/{document_id}/status',
        expect.objectContaining({
          params: { path: { document_id: 'doc-1' } },
          body: { stato: 'inviata' },
        }),
      )
    })
  })

  it('shows the server message when a transition is refused', async () => {
    mockPost.mockReturnValue(
      failed(
        { code: 'conflict', detail: "document: da 'bozza' non si puo' passare a 'accettata'" },
        409,
      ),
    )
    render(<OfferStatePicker document={OFFER} />, { wrapper })
    await userEvent.click(screen.getByRole('button', { name: 'Segna come Inviata' }))
    expect(await screen.findByRole('alert')).toHaveTextContent("non si puo' passare")
  })
})
