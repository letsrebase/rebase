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

const session = vi.hoisted(() => ({ role: 'admin' as 'admin' | 'collaborator' | 'readonly' }))
vi.mock('@/lib/auth', async () => {
  const { can } = await import('@/lib/permissions')
  return {
    useAuth: () => ({ user: { id: 'u-1', role: session.role } }),
    useCan: (action: Parameters<typeof can>[1]) => can(session.role, action),
  }
})
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn() } }
})
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => <a href={to}>{children}</a>,
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
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) }) as never
}
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
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
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
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 10, 10, 9, 0, 0))
  vi.mocked(api.GET).mockReset()
})
afterEach(() => vi.useRealTimers())

describe('HomeTaxDates', () => {
  it('shows the next two days with their distance, what falls due and «non calcolabile»', async () => {
    vi.mocked(api.GET).mockImplementation((() =>
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
    expect(screen.getByText('Secondo acconto 2026')).toBeInTheDocument()
    expect(screen.getByText('16/12/2026 · tra 36 giorni')).toBeInTheDocument()
    expect(screen.queryByText(/30\/06\/2026/)).not.toBeInTheDocument()
    // No date carries a net amount yet: a word, never a zero.
    expect(screen.getAllByText('non calcolabile')).toHaveLength(2)
    expect(screen.queryByText(/0,00/)).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Apri Tasse' })).toHaveAttribute('href', '/app/taxes')
    // The pack has dates for this year, so next year was never asked.
    expect(vi.mocked(api.GET)).toHaveBeenCalledTimes(1)
  })

  it('reads on into next year when fewer than two dates are left in this one', async () => {
    vi.mocked(api.GET).mockImplementation(((_path: string, options: { params: { query: { year: number } } }) =>
      options.params.query.year === 2026
        ? ok(calendar(2026, [entry('2026-11-30', 'second_advance')]))
        : ok(calendar(2027, [entry('2027-06-30', 'balance_and_first_advance')]))) as never)
    renderBlock()
    expect(await screen.findByText('30/11/2026 · tra 20 giorni')).toBeInTheDocument()
    expect(await screen.findByText('Saldo 2026 e primo acconto 2027')).toBeInTheDocument()
  })

  it('draws nothing when the pack carries no dates, and says nothing about it', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok(calendar(2026, []))) as never)
    const { container } = renderBlock()
    await waitFor(() => expect(vi.mocked(api.GET)).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument())
    expect(container).toBeEmptyDOMElement()
  })

  it('says «nessuna scadenza» in one line when the pack has dates and none is ahead', async () => {
    vi.mocked(api.GET).mockImplementation(((_path: string, options: { params: { query: { year: number } } }) =>
      options.params.query.year === 2026
        ? ok(calendar(2026, [entry('2026-06-30', 'balance_and_first_advance')]))
        : ok(calendar(2027, []))) as never)
    renderBlock()
    expect(await screen.findByText('Nessuna scadenza fiscale in vista.')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Apri Tasse' })).not.toBeInTheDocument()
  })

  it('says a failed read, with «Riprova», and never an empty block', async () => {
    vi.mocked(api.GET).mockImplementation((() => failed(500)) as never)
    renderBlock()
    expect(await screen.findByRole('alert')).toHaveTextContent('Non riesco a leggere le date.')
    expect(screen.getByRole('button', { name: 'Riprova' })).toBeInTheDocument()
    expect(screen.queryByText('Nessuna scadenza fiscale in vista.')).not.toBeInTheDocument()
  })

  it('draws nothing for an entity with no fiscal profile', async () => {
    vi.mocked(api.GET).mockImplementation((() => failed(404)) as never)
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
    vi.mocked(api.GET).mockImplementation(((
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
    expect(await screen.findByRole('heading', { name: 'Prossime tasse · Rossi Srl' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Prossime tasse · Bianchi' })).toBeInTheDocument()
    expect(await screen.findByText('30/11/2026 · tra 20 giorni')).toBeInTheDocument()
    expect(await screen.findByText('16/12/2026 · tra 36 giorni')).toBeInTheDocument()
  })

  it('names no entity in a one-entity space', async () => {
    vi.mocked(api.GET).mockImplementation((() => ok(calendar(2026, [entry('2026-11-30', 'second_advance')]))) as never)
    renderBlock()
    expect(await screen.findByRole('heading', { name: 'Prossime tasse' })).toBeInTheDocument()
    expect(screen.queryByText(/·.*Srl/)).not.toBeInTheDocument()
  })
})
