import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import type { InboxItem, InboxItemDetail } from './labels'

/** The fixtures and helpers shared by this folder's component tests. */

export const ME = 'u1'

export function item(overrides: Partial<InboxItem> = {}): InboxItem {
  return {
    id: '0199aaaa-0000-7000-8000-000000000001',
    source: 'upload',
    original_name: 'scontrino-trattoria.pdf',
    file_type: 'pdf',
    size_bytes: 12_345,
    has_file: true,
    sha256: 'a'.repeat(64),
    content_sha256: 'a'.repeat(64),
    natural_key: null,
    parent_id: null,
    uploaded_by_id: ME,
    legal_entity_id: null,
    kind: null,
    state: 'new',
    duplicate_of_id: null,
    reason: null,
    proposal_id: null,
    amount: null,
    currency: null,
    created_at: '2026-11-03T10:00:00Z',
    updated_at: '2026-11-03T10:00:00Z',
    ...overrides,
  }
}

export function detail(overrides: Partial<InboxItemDetail> = {}): InboxItemDetail {
  return { ...item(), extracted_text: null, parsed_fields: null, ...overrides } as InboxItemDetail
}

export function renderWithQuery(element: ReactElement): QueryClient {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}>{element}</QueryClientProvider>)
  return client
}

export function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) }
}

export function refused(status: number, problem: Record<string, unknown>) {
  return { error: problem, response: new Response(null, { status }) }
}

/** `window.matchMedia` for a viewport `width` wide: every `(min-width: Npx)` query it is asked. */
export function viewport(width: number) {
  window.matchMedia = ((query: string) => {
    const min = /min-width:\s*(\d+)px/.exec(query)
    return {
      matches: min === null ? false : width >= Number(min[1]),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }
  }) as never
}
