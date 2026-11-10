import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ok, refused, renderWithQuery, viewport } from '@/features/inbox/testing'
import { IvaPage } from './IvaPage'
import type { IvaSearch } from './search'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    to,
    params,
    children,
    className,
  }: {
    to: string
    params?: Record<string, string>
    children: React.ReactNode
    className?: string
  }) => (
    <a
      className={className}
      href={params ? to.replace(/\$(\w+)/g, (_, key: string) => params[key] ?? '') : to}
    >
      {children}
    </a>
  ),
}))

let role = 'admin'
vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ user: { id: 'u1', role }, isLoading: false }),
  useCan: () => role === 'admin',
}))

const ENTITY = 'a0000000-0000-7000-8000-000000000001'
const OTHER = 'a0000000-0000-7000-8000-000000000002'

const LADDER = {
  sales_vat: '1320.00',
  reverse_charge_vat: '0.00',
  purchase_vat: '440.00',
  credit_in: '0.00',
  debit_in: '0.00',
  surcharge: '0.00',
  result: '880.00',
  outcome: 'due',
}

function period(number: number, overrides: Record<string, unknown> = {}) {
  return {
    legal_entity_id: ENTITY,
    frequency: 'quarterly',
    year: 2026,
    number,
    label: `${number}° trimestre 2026`,
    period_start: '2026-01-01',
    period_end: '2026-03-31',
    state: 'ended',
    settled: true,
    ladder: LADDER,
    payment_due_on: '2026-05-18',
    due_item_id: 'd1',
    payment_state: 'expected',
    carry_forward_limit: null,
    ...overrides,
  }
}

const PERIODS = [
  period(1, { payment_state: 'settled' }),
  period(2, { ladder: { ...LADDER, result: '-310.00', outcome: 'credit' }, due_item_id: null, payment_state: null }),
  period(3, {
    ladder: { ...LADDER, sales_vat: '2200.00', result: '1860.00', credit_in: '310.00' },
    state: 'ended',
    payment_due_on: '2026-11-16',
  }),
  period(4, {
    state: 'open',
    settled: false,
    ladder: { ...LADDER, sales_vat: '120.00', purchase_vat: '0.00', result: '121.20', surcharge: '1.20' },
    payment_due_on: '2027-03-16',
    due_item_id: null,
    payment_state: null,
  }),
]

function invoiceRow(overrides: Record<string, unknown> = {}) {
  return {
    kind: 'invoice',
    invoice_id: 'i1',
    year: 2026,
    number: 7,
    issue_date: '2026-10-05',
    customer_id: 'c1',
    customer_name: 'Zanetti Impianti S.r.l.',
    status: 'issued',
    document_type: 'TD01',
    exigibility: 'immediate',
    groups: [{ rate: '22.00', nature: null, taxable_amount: '1000.00', vat_amount: '220.00' }],
    taxable_amount: '1000.00',
    vat_amount: '220.00',
    total: '1220.00',
    gap_reason: null,
    ...overrides,
  }
}

const SALES = {
  items: [
    invoiceRow(),
    invoiceRow({
      invoice_id: 'i2',
      number: 8,
      customer_name: 'Mixed S.p.A.',
      groups: [
        { rate: '22.00', nature: null, taxable_amount: '800.00', vat_amount: '176.00' },
        { rate: '0.00', nature: 'N2.1', taxable_amount: '200.00', vat_amount: '0.00' },
      ],
      taxable_amount: '1000.00',
      vat_amount: '176.00',
      total: '1176.00',
    }),
    invoiceRow({ invoice_id: 'i3', number: 9, status: 'cancelled', customer_name: 'Annullata S.r.l.' }),
    invoiceRow({
      invoice_id: 'i4',
      number: 10,
      customer_name: 'Comune di Lucca',
      exigibility: 'split_payment',
    }),
    {
      kind: 'gap',
      invoice_id: null,
      year: 2026,
      number: 11,
      issue_date: null,
      customer_id: null,
      customer_name: null,
      status: null,
      document_type: null,
      exigibility: null,
      groups: [],
      taxable_amount: null,
      vat_amount: null,
      total: null,
      gap_reason: 'prova di emissione',
    },
  ],
  total_count: 5,
  next_offset: null,
  summary: [
    { rate: '0.00', nature: 'N2.1', exigibility: 'immediate', taxable_amount: '200.00', vat_amount: '0.00' },
    { rate: '22.00', nature: null, exigibility: 'immediate', taxable_amount: '1800.00', vat_amount: '396.00' },
  ],
  totals: { taxable_amount: '3200.00', vat_amount: '616.00', vat_due: '396.00', total: '3816.00' },
}

function purchaseRow(overrides: Record<string, unknown> = {}) {
  return {
    purchase_id: 'p1',
    registered_on: '2026-10-12',
    document_date: '2026-10-10',
    number: 'FT/12',
    kind: 'invoice',
    supplier_id: 's1',
    supplier_name: 'Officine Bianchi S.r.l.',
    reverse_charge: false,
    split_payment: false,
    groups: [
      {
        rate: '22.00',
        nature: null,
        taxable_amount: '500.00',
        vat_amount: '110.00',
        deductible_share: '40.00',
        deductible_vat: '44.00',
      },
    ],
    taxable_amount: '500.00',
    vat_amount: '110.00',
    deductible_vat: '44.00',
    total: '610.00',
    ...overrides,
  }
}

const PURCHASES = {
  items: [purchaseRow(), purchaseRow({ purchase_id: 'p2', supplier_name: 'Foreign Ltd', reverse_charge: true })],
  total_count: 2,
  next_offset: null,
  summary: [
    {
      rate: '22.00',
      nature: null,
      deductible_share: '40.00',
      taxable_amount: '1000.00',
      vat_amount: '220.00',
      deductible_vat: '88.00',
    },
  ],
  totals: {
    taxable_amount: '1000.00',
    vat_amount: '220.00',
    deductible_vat: '88.00',
    reverse_charge_vat: '110.00',
    total: '1220.00',
  },
}

const EMPTY_SALES = {
  items: [],
  total_count: 0,
  next_offset: null,
  summary: [],
  totals: { taxable_amount: '0.00', vat_amount: '0.00', vat_due: '0.00', total: '0.00' },
}
const EMPTY_PURCHASES = {
  items: [],
  total_count: 0,
  next_offset: null,
  summary: [],
  totals: {
    taxable_amount: '0.00',
    vat_amount: '0.00',
    deductible_vat: '0.00',
    reverse_charge_vat: '0.00',
    total: '0.00',
  },
}

interface Answers {
  entities?: unknown
  periods?: unknown
  sales?: unknown
  purchases?: unknown
}

function answering(answers: Answers = {}) {
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    if (path === '/api/iva-periods/entities') {
      return Promise.resolve(
        answers.entities ?? ok([{ legal_entity_id: ENTITY, name: 'Lumen Studio S.r.l.', frequency: 'quarterly' }]),
      )
    }
    if (path === '/api/iva-periods') return Promise.resolve(answers.periods ?? ok(PERIODS))
    if (path === '/api/iva-registers/sales') return Promise.resolve(answers.sales ?? ok(SALES))
    if (path === '/api/iva-registers/purchases') return Promise.resolve(answers.purchases ?? ok(PURCHASES))
    return Promise.resolve(ok([]))
  }) as never)
}

const onSearch = vi.fn()

function renderPage(search: IvaSearch = {}) {
  return renderWithQuery(<IvaPage search={search} onSearch={onSearch} />)
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  onSearch.mockReset()
  role = 'admin'
  viewport(1440)
  vi.useRealTimers()
})

describe('«IVA» on a desk', () => {
  it('opens on the period running now, with its pill, the ladder and both registers counted', async () => {
    answering()
    renderPage()

    expect(await screen.findByRole('heading', { level: 1, name: 'IVA · 4° trimestre 2026' })).toBeInTheDocument()
    expect(screen.getByText('Lumen Studio S.r.l.')).toBeInTheDocument()
    expect(screen.getByText('Aperto')).toBeInTheDocument()
    expect(await screen.findByRole('tab', { name: 'Registro vendite (5)' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Registro acquisti (2)' })).toBeInTheDocument()
    // The ladder: the sales line, the deductible line, the credit, the running result «in corso».
    const ladder = await screen.findByRole('region', { name: 'Liquidazione' })
    expect(within(ladder).getByText(/In corso: il risultato cambia/)).toBeInTheDocument()
    expect(within(ladder).getByText('Meno IVA detraibile sugli acquisti')).toBeInTheDocument()
    expect(within(ladder).getByText('Meno credito del periodo precedente')).toBeInTheDocument()
    expect(within(ladder).getByText('Più maggiorazione trimestrale')).toBeInTheDocument()
    expect(within(ladder).getByText(/Scadenza del versamento: 16\/03\/2027 · dal pacchetto fiscale/)).toBeInTheDocument()
    // Nothing offers to close or reopen a period: the IVA period lock does not exist yet.
    expect(screen.queryByRole('button', { name: /chiudi il periodo|riapri/i })).not.toBeInTheDocument()
  })

  it('names each period with its result in the period select', async () => {
    answering()
    renderPage()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('combobox', { name: 'Periodo' }))

    expect(await screen.findByRole('option', { name: /^1° trimestre 2026 · 880,00\s€ · da versare$/ })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /^2° trimestre 2026 · -310,00\s€ · a credito$/ })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /^4° trimestre 2026 · 121,20\s€ · in corso$/ })).toBeInTheDocument()
    await user.click(screen.getByRole('option', { name: /2° trimestre 2026/ }))
    expect(onSearch).toHaveBeenCalledWith({ period: 2 })
  })

  it('draws a credit period with its pill and the credit the next one carries in', async () => {
    answering()
    renderPage({ period: 2 })
    expect(await screen.findByRole('heading', { level: 1, name: 'IVA · 2° trimestre 2026' })).toBeInTheDocument()
    const ladder = await screen.findByRole('region', { name: 'Liquidazione' })
    expect(within(ladder).getByText('A credito · 2° trimestre 2026')).toBeInTheDocument()
    expect(within(ladder).getAllByText('-310,00 €').length).toBeGreaterThan(0)
    expect(screen.getAllByText('A credito').length).toBeGreaterThan(0)
    expect(within(ladder).queryByText(/Scadenza del versamento/)).not.toBeInTheDocument()
  })

  it('shows the pill of a paid period and its payment date from the pack', async () => {
    answering()
    renderPage({ period: 1 })
    expect(await screen.findByText('Versato')).toBeInTheDocument()
    expect(await screen.findByText(/Scadenza del versamento: 18\/05\/2026 · dal pacchetto fiscale/)).toBeInTheDocument()
  })

  it('says why a small debit is carried, with the limit the pack sent, and no deadline', async () => {
    answering({
      periods: ok([
        period(1, {
          ladder: { ...LADDER, result: '60.00', outcome: 'carried', sales_vat: '60.00', purchase_vat: '0.00' },
          carry_forward_limit: '100.00',
          due_item_id: null,
          payment_state: null,
        }),
      ]),
    })
    renderPage()
    expect(await screen.findByText(/Il debito non supera 100,00 € \(limite del pacchetto fiscale\)/)).toBeInTheDocument()
    expect(screen.queryByText(/Scadenza del versamento/)).not.toBeInTheDocument()
  })

  it('reads «non calcolabile» for a period the server has no ladder for', async () => {
    answering({
      periods: ok([period(3, { state: 'ended', settled: false, ladder: null, payment_due_on: null, due_item_id: null, payment_state: null })]),
    })
    renderPage()
    expect(await screen.findByText('non calcolabile')).toBeInTheDocument()
    expect(screen.getAllByText('Non ancora liquidato').length).toBeGreaterThan(0)
    expect(screen.queryByText('Meno IVA detraibile sugli acquisti')).not.toBeInTheDocument()
  })

  it('lists the sales register one row per document, «2 aliquote» for a mixed one, with the totals of the period', async () => {
    answering()
    renderPage()
    const table = await screen.findByRole('table', { name: /Registro vendite/ })
    const rows = within(table).getAllByRole('row')
    // header, five documents, the totals row
    expect(rows).toHaveLength(7)
    expect(within(table).getByText('2 aliquote')).toBeInTheDocument()
    expect(within(table).getByText('Annullata')).toBeInTheDocument()
    expect(within(table).getByText('Scissione')).toBeInTheDocument()
    expect(within(table).getByText('Numero non utilizzato: prova di emissione')).toBeInTheDocument()
    expect(within(table).getByRole('link', { name: '2026/7' })).toHaveAttribute('href', '/app/invoices/i1')
    const totals = rows.at(-1) as HTMLElement
    expect(within(totals).getByText('Totale del periodo')).toBeInTheDocument()
    expect(within(totals).getByText('3.200,00 €')).toBeInTheDocument()
    expect(within(totals).getByText('Esigibile 396,00 €')).toBeInTheDocument()
    // The summary by rate, from the server.
    const summary = screen.getByRole('region', { name: 'Riepilogo per aliquota' })
    expect(within(summary).getByText('N2.1')).toBeInTheDocument()
    expect(within(summary).getByText('1.800,00 €')).toBeInTheDocument()
  })

  it('lists the purchase register with each share, the reverse charge and its summary', async () => {
    answering()
    renderPage({ register: 'purchases' })
    const table = await screen.findByRole('table', { name: /Registro acquisti/ })
    expect(within(table).getAllByText('Officine Bianchi S.r.l.')).toHaveLength(1)
    expect(within(table).getAllByText('40 %').length).toBeGreaterThan(0)
    expect(within(table).getByText('Inversione contabile')).toBeInTheDocument()
    expect(within(table).getByRole('link', { name: 'Officine Bianchi S.r.l.' })).toHaveAttribute(
      'href',
      '/app/purchases/p1',
    )
    expect(within(table).getByText('Totale del periodo')).toBeInTheDocument()
    expect(screen.getByText(/IVA dovuta per inversione contabile nel periodo/)).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Riepilogo per aliquota' })).toBeInTheDocument()
  })

  it('says «Nessuna registrazione in questo periodo.» in each register of an empty period, with the ladder at zero', async () => {
    answering({
      periods: ok([period(4, { state: 'open', settled: false, ladder: { ...LADDER, sales_vat: '0.00', purchase_vat: '0.00', result: '0.00', outcome: 'nil' }, due_item_id: null, payment_state: null })]),
      sales: ok(EMPTY_SALES),
      purchases: ok(EMPTY_PURCHASES),
    })
    renderPage()
    expect(await screen.findByText('Nessuna registrazione in questo periodo.')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Liquidazione' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Registro vendite (0)' })).toBeInTheDocument()
  })

  it('pages a register 50 at a time with «Mostra altri» and keeps the period totals', async () => {
    const calls: unknown[] = []
    vi.mocked(api.GET).mockImplementation(((path: string, init?: { params?: { query?: unknown } }) => {
      if (path === '/api/iva-periods/entities') {
        return Promise.resolve(ok([{ legal_entity_id: ENTITY, name: 'Lumen', frequency: 'quarterly' }]))
      }
      if (path === '/api/iva-periods') return Promise.resolve(ok(PERIODS))
      if (path === '/api/iva-registers/sales') {
        // The call's own query, typed by the router's generated types in the code under test.
        const query = init?.params?.query as Record<string, unknown>
        calls.push(query)
        const offset = query.offset
        return Promise.resolve(
          ok(
            offset === 0
              ? { ...SALES, items: [invoiceRow()], total_count: 212, next_offset: 50 }
              : { ...SALES, items: [invoiceRow({ invoice_id: 'i9', number: 51 })], total_count: 212, next_offset: null },
          ),
        )
      }
      return Promise.resolve(ok(PURCHASES))
    }) as never)
    renderPage()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Mostra altri (212 in tutto)' }))
    await waitFor(() => expect(screen.getByRole('link', { name: '2026/51' })).toBeInTheDocument())
    expect(calls).toEqual([
      expect.objectContaining({ year: 2026, number: 4, legal_entity_id: ENTITY, limit: 50, offset: 0 }),
      expect.objectContaining({ offset: 50 }),
    ])
    expect(screen.getByText('Esigibile 396,00 €')).toBeInTheDocument()
  })
})

describe('«IVA» states', () => {
  it('shows a wait with a sentence while the periods are read', async () => {
    vi.mocked(api.GET).mockImplementation((() => new Promise(() => {})) as never)
    renderPage()
    expect(await screen.findByRole('status', { name: 'Leggo i registri…' })).toBeInTheDocument()
  })

  it('shows the error with «Riprova» and no ladder when the periods cannot be read', async () => {
    answering({ periods: refused(500, { detail: 'Il server non risponde', code: 'x' }) })
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('Il server non risponde')
    expect(screen.getByRole('button', { name: 'Riprova' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Liquidazione' })).not.toBeInTheDocument()
  })

  it('says a register could not be read, shows no total and holds the ladder back', async () => {
    answering({ sales: refused(500, { detail: 'Registro non leggibile', code: 'x' }) })
    renderPage()
    expect(await screen.findByText('La liquidazione si mostra quando i due registri sono stati letti.')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Liquidazione' })).not.toBeInTheDocument()
    expect(screen.queryByText('Totale del periodo')).not.toBeInTheDocument()
  })

  it('answers a collaborator with one sentence and makes no request', async () => {
    role = 'collaborator'
    answering()
    renderPage()
    expect(await screen.findByText('L’IVA è visibile agli amministratori.')).toBeInTheDocument()
    expect(api.GET).not.toHaveBeenCalled()
  })

  it('says so when no legal entity settles IVA by period', async () => {
    answering({ entities: ok([]) })
    renderPage()
    expect(await screen.findByText(/Nessuna azienda di questo spazio liquida l’IVA per periodo/)).toBeInTheDocument()
    expect(api.GET).toHaveBeenCalledTimes(1)
  })

  it('offers its own «Azienda» select with several legal entities and says the IVA is not summed', async () => {
    answering({
      entities: ok([
        { legal_entity_id: ENTITY, name: 'Lumen Studio S.r.l.', frequency: 'quarterly' },
        { legal_entity_id: OTHER, name: 'Orbita S.r.l.', frequency: 'monthly' },
      ]),
    })
    renderPage({ entity: OTHER })
    expect(await screen.findByText('L’IVA non si somma tra aziende.')).toBeInTheDocument()
    const user = userEvent.setup()
    expect(await screen.findByRole('combobox', { name: 'Azienda' })).toHaveTextContent('Orbita S.r.l.')
    // The reading is one legal entity's: the request names it.
    await waitFor(() =>
      expect(api.GET).toHaveBeenCalledWith(
        '/api/iva-periods',
        expect.objectContaining({ params: { query: expect.objectContaining({ legal_entity_id: OTHER }) } }),
      ),
    )
    await user.click(screen.getByRole('combobox', { name: 'Azienda' }))
    await user.click(await screen.findByRole('option', { name: 'Lumen Studio S.r.l.' }))
    expect(onSearch).toHaveBeenCalledWith({ entity: ENTITY, period: undefined })
  })
})

describe('«IVA» on a phone', () => {
  beforeEach(() => viewport(390))

  it('opens on the ladder, then a segmented «Acquisti / Vendite» of cards', async () => {
    answering()
    renderPage()
    const ladder = await screen.findByRole('region', { name: 'Liquidazione' })
    expect(within(ladder).getByText(/Da versare · 4° trimestre 2026/)).toBeInTheDocument()
    expect(screen.queryByRole('table', { name: /Registro/ })).not.toBeInTheDocument()
    const tabs = screen.getAllByRole('tab').map((tab) => tab.textContent)
    expect(tabs).toEqual(['Registro acquisti (2)', 'Registro vendite (5)'])
    const cards = await screen.findByRole('list', { name: 'Registro vendite' })
    expect(within(cards).getAllByRole('listitem')).toHaveLength(5)
    expect(screen.getByText('IVA esigibile del periodo')).toBeInTheDocument()
  })
})
