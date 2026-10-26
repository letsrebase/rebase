import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { correspondentKeys } from './correspondents'
import { useDisconnectGmail } from './queries'
import { api } from '@/lib/api'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), DELETE: vi.fn() } }
})

describe('the addresses a customer page found', () => {
  it('are dropped when the mailbox is disconnected, not shown for the next one', async () => {
    vi.mocked(api.DELETE).mockResolvedValue({
      data: undefined,
      response: new Response(null, { status: 204 }),
    } as never)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    client.setQueryData(correspondentKeys.customer('c1'), { dominio: 'acme.it', corrispondenti: [] })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useDisconnectGmail(), { wrapper })

    result.current.mutate({ eliminaMessaggi: false })

    await waitFor(() => expect(client.getQueryData(correspondentKeys.customer('c1'))).toBeUndefined())
  })
})
