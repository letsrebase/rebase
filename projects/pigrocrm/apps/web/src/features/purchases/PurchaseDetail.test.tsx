import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { formatIsoDateItalian, toIsoDate } from '@/lib/dates'
import { ok, refused, renderWithQuery } from '@/features/inbox/testing'
import { PurchaseDetail } from './PurchaseDetail'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), DELETE: vi.fn() }, fetchWithRefresh: vi.fn() }
})

const navigate = vi.fn()
let writer = true

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    to,
    params,
    children,
  }: {
    to: string
    params?: Record<string, string>
    search?: unknown
    children: React.ReactNode
  }) => (
    <a href={params ? to.replace(/\$(\w+)/g, (_, key: string) => params[key] ?? '') : to}>{children}</a>
  ),
  useNavigate: () => navigate,
  useParams: () => ({ purchaseId: 'p1' }),
}))

vi.mock('@/lib/auth', () => ({
  useCan: () => writer,
  useCanWrite: () => writer,
  useAuth: () => ({ user: { id: 'u1', role: writer ? 'admin' : 'readonly' }, isLoading: false }),
}))

vi.mock('@rebase/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

vi.mock('./PurchaseFile', () => ({
  PurchaseFile: ({ inboxItemId }: { inboxItemId: string }) => <p>File di {inboxItemId}</p>,
}))

const CATEGORY = {
  id: 'cat1',
  name: 'Consulenze',
  position: 0,
  code: null,
  archived: false,
  created_at: 'x',
  updated_at: 'x',
}

const PURCHASE = {
  id: 'p1',
  kind: 'invoice',
  status: 'registered',
  legal_entity_id: null,
  deal_id: null,
  category_id: 'cat1',
  date: '2026-10-28',
  amount: '1464.00',
  description: '',
  supplier_id: null,
  merchant_name: 'Studio Rossi',
  number: '14/E',
  received_on: '2026-10-30',
  iva_period: '2026-10-15',
  currency: 'USD',
  currency_amount: '540.00',
  exchange_rate: '0.9206',
  exchange_rate_date: '2026-10-29',
  cost_amount: '1200.00',
  vat_lines: [
    { rate: '22.00', nature: null, taxable_amount: '1200.00', vat_amount: '264.00', deductible_share: '100.00', position: 0 },
  ],
  stamp_duty: null,
  withholding_rate: '20.00',
  withholding_base: '1350.00',
  withholding_amount: '270.00',
  fund_rate: null,
  fund_amount: null,
  split_payment: false,
  reverse_charge: false,
  corrects_purchase_id: null,
  inbox_item_id: null,
  document_id: null,
  custom_fields: {},
  created_at: '2026-11-03T09:00:00Z',
  updated_at: '2026-11-03T09:00:00Z',
}

const ITEM = { id: 'i1', has_file: true, original_name: 'f.pdf', file_type: 'pdf', updated_at: '2026-11-03T10:00:00Z' }

function answering(purchase: unknown, item: unknown = ITEM) {
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    if (path === '/api/purchases/{purchase_id}') return Promise.resolve(purchase)
    if (path === '/api/cost-categories') return Promise.resolve(ok([CATEGORY]))
    if (path === '/api/inbox/items/{item_id}') return Promise.resolve(ok(item))
    if (String(path).includes('timeline')) return Promise.resolve(ok([]))
    return Promise.resolve(ok({}))
  }) as never)
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.DELETE).mockReset()
  navigate.mockReset()
  writer = true
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})

describe('the page of a purchase', () => {
  it('reads the document as stored, with the VAT lines and the cost', async () => {
    answering(ok(PURCHASE))
    renderWithQuery(<PurchaseDetail />)
    expect(await screen.findByRole('heading', { level: 1, name: 'Studio Rossi' })).toBeInTheDocument()
    expect(screen.getByText('Registrata')).toHaveAttribute('data-tone', 'ink')
    expect(screen.getByText('USD 540,00')).toBeInTheDocument()
    expect(screen.getByText(/0,9206 del 29\/10\/2026/)).toBeInTheDocument()
    expect(screen.getByText('Costo imputato')).toBeInTheDocument()
    expect(screen.getByText(/20 % · 270,00/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Righe IVA' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Quota detraibile' })).toBeInTheDocument()
    expect(screen.getByText('264,00 €')).toBeInTheDocument()
    expect(screen.getByText('30/10/2026')).toBeInTheDocument()
    expect(screen.getByText('15/10/2026')).toBeInTheDocument()
    expect(screen.queryByText(/\d{4}-\d{2}-\d{2}/)).not.toBeInTheDocument()
    // Due items and the integration are not built.
    expect(screen.queryByText(/Scadenze|Integrazione|Segna come pagata/)).not.toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Timeline' })).toBeInTheDocument()
  })

  it('offers «Modifica», «Annulla acquisto» and «Elimina» to a writer', async () => {
    answering(ok(PURCHASE))
    renderWithQuery(<PurchaseDetail />)
    expect(await screen.findByRole('link', { name: /Modifica/ })).toHaveAttribute('href', '/app/purchases/edit/p1')
    expect(screen.getByRole('button', { name: 'Annulla acquisto' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Elimina' })).toBeInTheDocument()
  })

  it('says the period is closed in place of «Modifica», «Annulla acquisto» and «Elimina»', async () => {
    answering(ok({ ...PURCHASE, iva_period_closed: true }))
    renderWithQuery(<PurchaseDetail />)
    expect(await screen.findByText('Periodo IVA chiuso: non si può modificare.')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Modifica/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Annulla acquisto' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Elimina' })).not.toBeInTheDocument()
  })

  it('offers nothing to change to a read-only member', async () => {
    writer = false
    answering(ok(PURCHASE))
    renderWithQuery(<PurchaseDetail />)
    await screen.findByRole('heading', { level: 1, name: 'Studio Rossi' })
    expect(screen.queryByRole('link', { name: /Modifica/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Annulla acquisto' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Elimina' })).not.toBeInTheDocument()
  })

  it('shows a cancelled purchase as such, with no edit and no second cancel', async () => {
    answering(ok({ ...PURCHASE, status: 'cancelled' }))
    renderWithQuery(<PurchaseDetail />)
    expect(await screen.findByText('Annullato')).toHaveAttribute('data-tone', 'muted')
    expect(screen.queryByRole('link', { name: /Modifica/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Annulla acquisto' })).not.toBeInTheDocument()
  })

  it('cancels after a confirmation and shows a refusal in the server words', async () => {
    answering(ok(PURCHASE))
    vi.mocked(api.POST).mockResolvedValue(
      refused(409, { title: 'Conflitto', detail: 'Periodo IVA chiuso: non si può modificare.' }) as never,
    )
    renderWithQuery(<PurchaseDetail />)
    await userEvent.click(await screen.findByRole('button', { name: 'Annulla acquisto' }))
    expect(window.confirm).toHaveBeenCalled()
    expect(api.POST).toHaveBeenCalledWith(
      '/api/purchases/{purchase_id}/cancel',
      expect.objectContaining({ params: { path: { purchase_id: 'p1' } } }),
    )
    expect(await screen.findByText('Periodo IVA chiuso: non si può modificare.')).toBeInTheDocument()
  })

  it('does nothing when the confirmation is refused', async () => {
    vi.mocked(window.confirm).mockReturnValue(false)
    answering(ok(PURCHASE))
    renderWithQuery(<PurchaseDetail />)
    await userEvent.click(await screen.findByRole('button', { name: 'Elimina' }))
    expect(api.DELETE).not.toHaveBeenCalled()
  })

  it('deletes and goes back to the list', async () => {
    answering(ok(PURCHASE))
    vi.mocked(api.DELETE).mockResolvedValue(ok({}) as never)
    renderWithQuery(<PurchaseDetail />)
    await userEvent.click(await screen.findByRole('button', { name: 'Elimina' }))
    await vi.waitFor(() => expect(navigate).toHaveBeenCalledWith({ to: '/app/purchases' }))
  })

  it('names where a registered purchase came from and shows its file under «Documenti»', async () => {
    // Late evening UTC: the day shown is the local one, never the first ten characters.
    const created = '2026-11-03T23:30:00Z'
    answering(ok({ ...PURCHASE, inbox_item_id: 'i1', created_at: created }))
    renderWithQuery(<PurchaseDetail />)
    expect(
      await screen.findByText(`Registrata da In arrivo il ${formatIsoDateItalian(toIsoDate(new Date(created)))}`),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Apri In arrivo' })).toHaveAttribute('href', '/app/inbox')
    await userEvent.click(screen.getByRole('tab', { name: 'Documenti' }))
    expect(await screen.findByText('File di i1')).toBeInTheDocument()
  })

  it('reads the timeline of the purchase under «Timeline»', async () => {
    answering(ok(PURCHASE))
    renderWithQuery(<PurchaseDetail />)
    await userEvent.click(await screen.findByRole('tab', { name: 'Timeline' }))
    await vi.waitFor(() =>
      expect(
        (vi.mocked(api.GET).mock.calls as unknown as [string][]).some(([path]) => path.includes('timeline')),
      ).toBe(true),
    )
  })

  it('says no file is attached when there is no inbox item', async () => {
    answering(ok(PURCHASE))
    renderWithQuery(<PurchaseDetail />)
    await userEvent.click(await screen.findByRole('tab', { name: 'Documenti' }))
    expect(await screen.findByText('Nessun file allegato a questo acquisto.')).toBeInTheDocument()
    expect(screen.queryByRole('complementary', { name: 'Origine' })).not.toBeInTheDocument()
  })

  it('waits with «Leggo l’acquisto…»', async () => {
    vi.mocked(api.GET).mockImplementation((() => new Promise(() => {})) as never)
    renderWithQuery(<PurchaseDetail />)
    expect(await screen.findByRole('status', { name: 'Leggo l’acquisto…' })).toBeInTheDocument()
  })

  it('shows a failed read with «Riprova»', async () => {
    answering(refused(500, { title: 'Errore', detail: 'Il server non risponde.' }))
    renderWithQuery(<PurchaseDetail />)
    expect(await screen.findByText('Il server non risponde.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Riprova' })).toBeInTheDocument()
  })

  it('says when the purchase is not found', async () => {
    answering(ok(null))
    renderWithQuery(<PurchaseDetail />)
    expect(await screen.findByText('Acquisto non trovato.')).toBeInTheDocument()
  })
})
