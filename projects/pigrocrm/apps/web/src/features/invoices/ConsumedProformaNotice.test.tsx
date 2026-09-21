import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ConsumedProformaNotice } from './ConsumedProformaNotice'
import type { Invoice } from './queries'
import { api } from '@/lib/api'

// The route tree is not mounted here, so `Link` renders as the anchor it would become.
vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    Link: ({
      to,
      params,
      children,
    }: {
      to: string
      params?: Record<string, string>
      children: ReactNode
    }) => (
      <a href={to.replace('$invoiceId', params?.invoiceId ?? '')}>{children}</a>
    ),
  }
})

const mockGet = vi.spyOn(api, 'GET')

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

const CONSUMED = { id: 'pf-1', tipo: 'proforma', stato: 'consumata' } as unknown as Invoice
const FATTURA = {
  id: 'inv-18',
  tipo: 'fattura',
  stato: 'emessa',
  anno: 2026,
  numero: 18,
  riferimento: null,
  origine_proforma_id: 'pf-1',
} as unknown as Invoice

function wrap(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{children}</QueryClientProvider>)
}

beforeEach(() => {
  mockGet.mockReset()
})

describe('ConsumedProformaNotice', () => {
  /** The page Ivan was stuck on: a consumed proforma with no actions and no XML, and no
   *  word about where they went (ORB-134). */
  it('names the fattura the proforma became and links to its page', async () => {
    mockGet.mockResolvedValue(ok({ items: [FATTURA], next_cursor: null }))
    wrap(<ConsumedProformaNotice proforma={CONSUMED} />)

    const link = await screen.findByRole('link', { name: '2026/18' })
    expect(link).toHaveAttribute('href', '/app/invoices/inv-18')
    expect(screen.getByTestId('consumed-proforma-notice').textContent).toContain(
      'emessa come fattura',
    )
    const [path, options] = mockGet.mock.calls[0] as unknown as [string, { params: unknown }]
    expect(path).toBe('/api/invoices')
    expect(options.params).toEqual({
      query: { origine_proforma_id: 'pf-1', tipo: 'fattura', limit: 1 },
    })
  })

  it('still says where to look when the list answers nothing', async () => {
    mockGet.mockResolvedValue(ok({ items: [], next_cursor: null }))
    wrap(<ConsumedProformaNotice proforma={CONSUMED} />)

    expect(await screen.findByTestId('consumed-proforma-notice')).toHaveTextContent(
      'elenco delle fatture',
    )
    expect(screen.queryByRole('link')).toBeNull()
  })
})
