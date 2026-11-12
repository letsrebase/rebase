/**
 * The Home's tax dates (REB-1026, the brief's «The Home's tax dates»): the next two dates of a
 * legal entity with the amount of each, nothing when the pack has no dates, one line when
 * nothing falls due, and nothing requested for a role or a module that may not read them.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { LegalEntityContext, type LegalEntityValue } from '@/lib/legalEntity'
import { spaceModules } from '@/lib/modules.testing'
import { queryKeys } from '@/lib/query'
import { HomeTaxDates } from './TaxDates'

const session = vi.hoisted(() => ({
  role: 'admin' as 'admin' | 'collaborator' | 'readonly',
  /** A role that reads the calendar and not the due items (the two gates are separate). */
  noItems: false,
}))
vi.mock('@/lib/auth', async () => {
  const { can } = await import('@/lib/permissions')
  return {
    useAuth: () => ({ user: { id: 'u-1', role: session.role } }),
    useCan: (action: Parameters<typeof can>[1]) =>
      !(session.noItems && action === 'read_fiscal_due_items') && can(session.role, action),
  }
})
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => (
    <a href={to}>{children}</a>
  ),
}))

const entry = (due: string, kind: string, tax = 'substitute_tax') => ({
  nominal_date: due,
  due_date: due,
  tax,
  kind,
  cadence: null,
  covers: 'x',
  source: 'Agenzia delle Entrate',
})
const calendar = (year: number, dates: unknown[], legalEntity = 'le-1') => ({
  year,
  legal_entity_id: legalEntity,
  pack_id: 'it-flat-rate',
  pack_version: '1',
  dates,
})

function ok(data: unknown) {
  return Promise.resolve({
    data,
    response: new Response(null, { status: 200 }),
  }) as never
}
const TAX_ITEM = {
  id: 'i-1',
  direction: 'out',
  state: 'expected',
  tax: 'substitute_tax',
  amount: '700.00',
  currency: 'EUR',
  settled: '0.00',
  description: 'Secondo acconto imposta sostitutiva 2026',
  source_kind: 'tax_advance',
  due_date: '2026-11-30',
  carry_to_confirm: false,
}
const taxItem = (overrides: Record<string, unknown> = {}) => ({
  ...TAX_ITEM,
  ...overrides,
})

let dueItems: unknown[] = []
let itemsFailure: number | null = null

/** Answers the calendar through `calendarFor` and the due items with `dueItems`. */
function serve(calendarFor: (path: string, options: never) => unknown) {
  vi.mocked(api.GET).mockImplementation(((path: string, options: never) => {
    if (path !== '/api/due-items') return calendarFor(path, options)
    return itemsFailure === null ? ok({ items: dueItems, next_cursor: null }) : failed(itemsFailure)
  }) as never)
}
const callsTo = (path: string) =>
  (vi.mocked(api.GET).mock.calls as unknown as [string][]).filter(([called]) => called === path)
const calendarCalls = () => callsTo('/api/analytics/fiscal/calendar')
const itemCalls = () => callsTo('/api/due-items')

function failed(status: number, detail = 'Non riesco a leggere le date.') {
  return Promise.resolve({
    error: { detail, status },
    response: new Response(null, { status }),
  }) as never
}

const ONE: LegalEntityValue = {
  legal_entities: [],
  selected: null,
  select: () => {},
  several: false,
  scoped: false,
  pinned: false,
  loaded: true,
  byId: () => undefined,
}

function renderBlock(off: Parameters<typeof spaceModules>[0] = [], legal: LegalEntityValue = ONE) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  client.setQueryData(queryKeys.modules, spaceModules(off, 'solo'))
  return render(
    <QueryClientProvider client={client}>
      <LegalEntityContext.Provider value={legal}>
        <HomeTaxDates variant="block" />
      </LegalEntityContext.Provider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  session.role = 'admin'
  session.noItems = false
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 10, 10, 9, 0, 0))
  vi.mocked(api.GET).mockReset()
  dueItems = []
  itemsFailure = null
})
afterEach(() => vi.useRealTimers())

describe('HomeTaxDates', () => {
  it('shows the next two days with their distance, what falls due and «non calcolabile»', async () => {
    serve((() =>
      ok(
        calendar(2026, [
          entry('2026-06-30', 'balance_and_first_advance'),
          entry('2026-11-30', 'second_advance'),
          entry('2026-12-16', 'instalments_end'),
        ]),
      )) as never)
    renderBlock()
    expect(await screen.findByText('30/11/2026 · tra 20 giorni')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Prossime tasse' })).toBeInTheDocument()
    expect(screen.getByText('Secondo acconto 2026 · Imposta sostitutiva')).toBeInTheDocument()
    expect(screen.getByText('16/12/2026 · tra 36 giorni')).toBeInTheDocument()
    expect(screen.queryByText(/30\/06\/2026/)).not.toBeInTheDocument()
    // No date carries a net amount yet: a word, never a zero.
    expect(screen.getAllByText('non calcolabile')).toHaveLength(2)
    expect(screen.queryByText(/0,00/)).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Apri Tasse' })).toHaveAttribute('href', '/app/taxes')
    // The pack has dates for this year, so next year was never asked.
    expect(calendarCalls()).toHaveLength(1)
  })

  it('reads on into next year when fewer than two dates are left in this one', async () => {
    serve(((_path: string, options: { params: { query: { year: number } } }) =>
      options.params.query.year === 2026
        ? ok(calendar(2026, [entry('2026-11-30', 'second_advance')]))
        : ok(calendar(2027, [entry('2027-06-30', 'balance_and_first_advance')]))) as never)
    renderBlock()
    expect(await screen.findByText('30/11/2026 · tra 20 giorni')).toBeInTheDocument()
    expect(
      await screen.findByText('Saldo 2026 e primo acconto 2027 · Imposta sostitutiva'),
    ).toBeInTheDocument()
  })

  it('draws nothing when the pack carries no dates, and says nothing about it', async () => {
    serve((() => ok(calendar(2026, []))) as never)
    const { container } = renderBlock()
    await waitFor(() => expect(calendarCalls()).toHaveLength(2))
    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument())
    expect(container).toBeEmptyDOMElement()
  })

  it('says «nessuna scadenza» in one line when the pack has dates and none is ahead', async () => {
    serve(((_path: string, options: { params: { query: { year: number } } }) =>
      options.params.query.year === 2026
        ? ok(calendar(2026, [entry('2026-06-30', 'balance_and_first_advance')]))
        : ok(calendar(2027, []))) as never)
    renderBlock()
    expect(await screen.findByText('Nessuna scadenza fiscale in vista.')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Apri Tasse' })).not.toBeInTheDocument()
  })

  it('says a failed read, with «Riprova», and never an empty block', async () => {
    serve((() => failed(500)) as never)
    renderBlock()
    expect(await screen.findByRole('alert')).toHaveTextContent('Non riesco a leggere le date.')
    expect(screen.getByRole('button', { name: 'Riprova' })).toBeInTheDocument()
    expect(screen.queryByText('Nessuna scadenza fiscale in vista.')).not.toBeInTheDocument()
  })

  it('draws nothing for an entity with no fiscal profile', async () => {
    serve((() => failed(404)) as never)
    const { container } = renderBlock()
    await waitFor(() => expect(vi.mocked(api.GET)).toHaveBeenCalled())
    await waitFor(() => expect(container).toBeEmptyDOMElement())
  })

  it('asks for nothing and draws nothing for a role that may not read the figures', () => {
    session.role = 'collaborator'
    const { container } = renderBlock()
    expect(container).toBeEmptyDOMElement()
    expect(vi.mocked(api.GET)).not.toHaveBeenCalled()
  })

  it('asks for nothing and draws nothing with the taxes module off', () => {
    const { container } = renderBlock(['taxes'])
    expect(container).toBeEmptyDOMElement()
    expect(vi.mocked(api.GET)).not.toHaveBeenCalled()
  })

  it('names each legal entity in «Tutte le aziende» and merges none of them', async () => {
    serve(((
      _path: string,
      options: { params: { query: { year: number; legal_entity_id: string } } },
    ) => {
      const { year, legal_entity_id: id } = options.params.query
      const dates =
        year !== 2026
          ? []
          : id === 'le-1'
            ? [entry('2026-11-30', 'second_advance')]
            : [entry('2026-12-16', 'instalments_end', 'income_tax')]
      return ok(calendar(year, dates, id))
    }) as never)
    const entity = (id: string, name: string) => ({ id, name }) as never
    renderBlock([], {
      ...ONE,
      several: true,
      legal_entities: [entity('le-1', 'Rossi Srl'), entity('le-2', 'Bianchi')],
    })
    expect(
      await screen.findByRole('heading', {
        name: 'Prossime tasse · Rossi Srl',
      }),
    ).toBeInTheDocument()
    expect(
      await screen.findByRole('heading', { name: 'Prossime tasse · Bianchi' }),
    ).toBeInTheDocument()
    expect(await screen.findByText('30/11/2026 · tra 20 giorni')).toBeInTheDocument()
    expect(await screen.findByText('16/12/2026 · tra 36 giorni')).toBeInTheDocument()
  })

  it('names no entity in a one-entity space', async () => {
    serve((() => ok(calendar(2026, [entry('2026-11-30', 'second_advance')]))) as never)
    renderBlock()
    expect(await screen.findByRole('heading', { name: 'Prossime tasse' })).toBeInTheDocument()
    expect(screen.queryByText(/·.*Srl/)).not.toBeInTheDocument()
  })

  describe('with the tax due items', () => {
    const second = () =>
      calendar(2026, [
        entry('2026-11-30', 'second_advance'),
        entry('2026-12-16', 'instalments_end'),
      ])

    it('shows an item with its amount and state, and drops the pack line it covers that day', async () => {
      dueItems = [taxItem()]
      serve(() => ok(second()))
      renderBlock()
      expect(
        await screen.findByText('Secondo acconto imposta sostitutiva 2026'),
      ).toBeInTheDocument()
      expect(screen.getByText(/700,00/)).toBeInTheDocument()
      expect(screen.getByText('Da pagare')).toBeInTheDocument()
      expect(
        screen.queryByText('Secondo acconto 2026 · Imposta sostitutiva'),
      ).not.toBeInTheDocument()
      // The other day has no item: the pack's line stays, with no amount.
      expect(
        screen.getByText('Ultima rata di saldo 2025 e primo acconto 2026 · Imposta sostitutiva'),
      ).toBeInTheDocument()
      expect(screen.getAllByText('non calcolabile')).toHaveLength(1)
    })

    it('keeps the pack line of a tax the day carries no item for', async () => {
      dueItems = [taxItem({ tax: 'inps' })]
      serve(() => ok(second()))
      renderBlock()
      expect(
        await screen.findByText('Secondo acconto 2026 · Imposta sostitutiva'),
      ).toBeInTheDocument()
      expect(screen.getByText('Secondo acconto imposta sostitutiva 2026')).toBeInTheDocument()
    })

    it('counts a paid item as covering its pack line, and draws neither', async () => {
      dueItems = [taxItem({ state: 'settled', settled: '700.00' })]
      serve(() => ok(second()))
      renderBlock()
      expect(await screen.findByText('16/12/2026 · tra 36 giorni')).toBeInTheDocument()
      expect(screen.queryByText(/30\/11\/2026/)).not.toBeInTheDocument()
      expect(screen.queryByText('Pagato')).not.toBeInTheDocument()
      expect(
        screen.queryByText('Secondo acconto 2026 · Imposta sostitutiva'),
      ).not.toBeInTheDocument()
    })

    it('puts an overdue item first, ahead of the next date', async () => {
      dueItems = [
        taxItem({
          id: 'i-late',
          due_date: '2026-11-02',
          description: 'Saldo imposta sostitutiva 2025',
        }),
      ]
      serve(() => ok(second()))
      renderBlock()
      expect(await screen.findByText('Saldo imposta sostitutiva 2025')).toBeInTheDocument()
      expect(screen.getByText('Scaduto')).toBeInTheDocument()
      const days = screen.getAllByText(/^\d\d\/\d\d\/2026 · /).map((node) => node.textContent)
      expect(days).toEqual(['02/11/2026 · 8 giorni fa', '30/11/2026 · tra 20 giorni'])
    })

    it('shows the calendar alone, and asks for no item, for a role that may not read them', async () => {
      session.noItems = true
      dueItems = [taxItem()]
      serve(() => ok(second()))
      renderBlock()
      expect(
        await screen.findByText('Secondo acconto 2026 · Imposta sostitutiva'),
      ).toBeInTheDocument()
      expect(screen.getAllByText('non calcolabile')).toHaveLength(2)
      expect(itemCalls()).toHaveLength(0)
    })

    it('still shows the dates when the items cannot be read', async () => {
      itemsFailure = 500
      serve(() => ok(second()))
      renderBlock()
      expect(
        await screen.findByText('Secondo acconto 2026 · Imposta sostitutiva'),
      ).toBeInTheDocument()
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    })
  })
})
