import { screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { LegalEntityContext, type LegalEntityValue } from '@/lib/legalEntity'
import { ContractDays } from './ContractDays'
import { CONTRACT_ID, CUSTOMER_ID, ok, renderWithQuery, row } from './testing'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() } }
})
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, params, search, children }: { to: string; params?: Record<string, string>; search?: Record<string, string>; children: React.ReactNode }) => (
    <a href={(params ? to.replace(/\$(\w+)/g, (_, key: string) => params[key] ?? '') : to) + (search ? `?${new URLSearchParams(search).toString()}` : '')}>{children}</a>
  ),
}))
let canWrite = true
vi.mock('@/lib/auth', () => ({ useCan: () => canWrite }))
vi.mock('@/features/gmail/queries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/gmail/queries')>()
  return { ...actual, useGmailConfigured: () => true }
})

beforeEach(() => {
  canWrite = true
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.GET).mockResolvedValue(ok({ items: [row({ id: 'd1', descrizione: 'Workshop' })], troncato: false }) as never)
})

describe('a contract’s days on its own page', () => {
  it('does not narrow a contract’s days by the sidebar’s azienda: the contract names its own', async () => {
    const other: LegalEntityValue = {
      aziende: [],
      selected: 'azienda-b',
      select: vi.fn(),
      several: true,
      scoped: false,
      pinned: false,
      byId: () => undefined,
    } as unknown as LegalEntityValue
    renderWithQuery(
      <LegalEntityContext value={other}>
        <ContractDays contractId={CONTRACT_ID} customerId={CUSTOMER_ID} requiresPriorApproval={false} />
      </LegalEntityContext>,
    )
    await screen.findByRole('link', { name: /Workshop/ }, { timeout: 5000 })
    const call = (vi.mocked(api.GET).mock.calls as unknown as [string, { params: { query: Record<string, unknown> } }][]).find(
      ([path]) => path === '/api/work-units',
    )
    expect(call?.[1].params.query).toMatchObject({ contract_id: CONTRACT_ID })
    expect(call?.[1].params.query.azienda_id).toBeUndefined()
  })

  it('lists them and offers the three things a person does with them', async () => {
    renderWithQuery(<ContractDays contractId={CONTRACT_ID} customerId={CUSTOMER_ID} requiresPriorApproval />)
    expect(await screen.findByRole('link', { name: /Workshop/ }, { timeout: 5000 })).toHaveAttribute('href', '/app/days/d1')
    expect(screen.getByRole('button', { name: 'Chiedi approvazione' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Registra approvazione/ })).toHaveAttribute(
      'href',
      `/app/approvals/new?contract=${CONTRACT_ID}`,
    )
    expect(screen.getByRole('link', { name: /Registra giornata/ })).toHaveAttribute(
      'href',
      `/app/days/new?contract=${CONTRACT_ID}`,
    )
  })

  it('does not offer to ask for an approval a contract does not require', async () => {
    renderWithQuery(<ContractDays contractId={CONTRACT_ID} customerId={CUSTOMER_ID} requiresPriorApproval={false} />)
    await screen.findByRole('link', { name: /Workshop/ })
    expect(screen.queryByRole('button', { name: 'Chiedi approvazione' })).not.toBeInTheDocument()
  })

  it('shows a reader the days and none of the actions', async () => {
    canWrite = false
    renderWithQuery(<ContractDays contractId={CONTRACT_ID} customerId={CUSTOMER_ID} requiresPriorApproval />)
    await screen.findByRole('link', { name: /Workshop/ })
    expect(screen.queryByRole('button', { name: 'Chiedi approvazione' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Registra/ })).not.toBeInTheDocument()
  })
})
