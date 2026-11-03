import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { CustomerPicker, type PickedCustomer } from './CustomerPicker'

const mockGet = vi.spyOn(api, 'GET')

const ACME = { id: 'cust-acme', ragione_sociale: 'ACME Srl' }
const ZETA = { id: 'cust-zeta', ragione_sociale: 'Zeta Srl' }

function answer(items: unknown[], nextCursor: string | null = null) {
  return Promise.resolve({
    data: { items, next_cursor: nextCursor },
    response: new Response(null, { status: 200 }),
  }) as never
}

beforeEach(() => {
  mockGet.mockReset()
  mockGet.mockImplementation(((path: string) => {
    if (path === '/api/customers/{customer_id}') {
      return Promise.resolve({ data: ACME, response: new Response(null, { status: 200 }) }) as never
    }
    return answer([ACME, ZETA])
  }) as never)
})

function Harness({
  initial = null,
  onPick,
}: {
  initial?: string | null
  onPick?: (customer: PickedCustomer | null) => void
}) {
  const [value, setValue] = useState<string | null>(initial)
  return (
    <CustomerPicker
      id="cliente"
      value={value}
      onChange={(customer) => {
        setValue(customer?.id ?? null)
        onPick?.(customer)
      }}
    />
  )
}

function renderPicker(props: Parameters<typeof Harness>[0] = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <Harness {...props} />
    </QueryClientProvider>,
  )
}

function lastQuery(): Record<string, unknown> {
  const calls = mockGet.mock.calls.filter((call) => call[0] === '/api/customers')
  return (calls.at(-1)?.[1] as unknown as { params: { query: Record<string, unknown> } }).params.query
}

describe('CustomerPicker', () => {
  it('lists a few matches from the server, never the whole customer list', async () => {
    renderPicker()

    expect(await screen.findByRole('button', { name: 'ACME Srl' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Zeta Srl' })).toBeInTheDocument()
    expect(lastQuery().limit).toBeLessThan(200)
  })

  it('searches the server with what was typed, once the box has settled', async () => {
    renderPicker()
    await screen.findByRole('button', { name: 'ACME Srl' })

    await userEvent.type(screen.getByRole('textbox', { name: 'Cerca un cliente' }), 'zet')

    await waitFor(() => expect(lastQuery().search).toBe('zet'))
  })

  it('says when more customers exist than the matches shown', async () => {
    mockGet.mockImplementation((() => answer([ACME], 'next')) as never)
    renderPicker()

    expect(await screen.findByText(/Ci sono altri clienti/)).toBeInTheDocument()
  })

  it('hands the picked customer to the caller and shows it by name, with a way to remove it', async () => {
    const onPick = vi.fn()
    renderPicker({ onPick })

    await userEvent.click(await screen.findByRole('button', { name: 'Zeta Srl' }))

    expect(onPick).toHaveBeenCalledWith(expect.objectContaining(ZETA))
    expect(screen.getByText('Zeta Srl')).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(mockGet).not.toHaveBeenCalledWith('/api/customers/{customer_id}', expect.anything())

    await userEvent.click(screen.getByRole('button', { name: 'Rimuovi il cliente' }))

    expect(onPick).toHaveBeenLastCalledWith(null)
    expect(await screen.findByRole('textbox', { name: 'Cerca un cliente' })).toBeInTheDocument()
  })

  it('names a customer it was only given the id of, without searching for others', async () => {
    renderPicker({ initial: 'cust-acme' })

    expect(await screen.findByText('ACME Srl')).toBeInTheDocument()
    expect(mockGet).not.toHaveBeenCalledWith('/api/customers', expect.anything())
  })
})
