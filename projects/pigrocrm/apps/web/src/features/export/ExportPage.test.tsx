/**
 * «Esporta per il commercialista» (REB-1028, the approved brief of 2026-11-10): the form that
 * asks for a build, the list of stored exports and the panel that downloads one, and the rules
 * the brief gives a component that it must not break: «Tutte le aziende» never preselected, no
 * part or sentence for a module that is off, no delivery that does not exist yet, an admin's page.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent, { type UserEvent } from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { downloadFromApi } from '@/lib/download'
import { LegalEntityContext, type LegalEntityValue } from '@/lib/legalEntity'
import { ExportPage } from './ExportPage'
import type { StoredExport } from './labels'
import type { ExportTab } from './search'

const session = vi.hoisted(() => ({ role: 'admin' as 'admin' | 'collaborator' | 'readonly' }))
vi.mock('@/lib/auth', async () => {
  const { can } = await import('@/lib/permissions')
  return {
    useAuth: () => ({ user: { id: 'u-1', name: 'Anna', role: session.role } }),
    useCan: (action: Parameters<typeof can>[1]) => can(session.role, action),
    useIsAdmin: () => session.role === 'admin',
  }
})
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() } }
})
vi.mock('@/lib/download', () => ({ downloadFromApi: vi.fn(() => Promise.resolve()) }))
vi.mock('@rebase/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const ok = (data: unknown) => Promise.resolve({ data, response: new Response(null, { status: 200 }) })
const refused = (status: number, problem: Record<string, unknown>) =>
  Promise.resolve({ error: problem, response: new Response(null, { status }) })

const ONE = { id: 'le-1', name: 'Lumen Studio S.r.l.', active: true } as unknown as LegalEntityValue['legal_entities'][number]
const TWO = { id: 'le-2', name: 'Rebase Ltd', active: true } as unknown as LegalEntityValue['legal_entities'][number]

function companies(overrides: Partial<LegalEntityValue> = {}): LegalEntityValue {
  return {
    legal_entities: [ONE],
    selected: null,
    select: vi.fn(),
    several: false,
    scoped: false,
    pinned: false,
    loaded: true,
    byId: (id) => [ONE, TWO].find((entity) => entity.id === id),
    ...overrides,
  }
}

const SEVERAL = companies({ legal_entities: [ONE, TWO], several: true })

const PARTS = [
  { key: 'sales_register', label: 'Registro delle vendite', date_rule: 'per data di emissione', contents: 'Elenco CSV' },
  { key: 'issued_invoice_files', label: 'Fatture emesse: file originali', date_rule: 'per data del documento', contents: 'File originali' },
]

const line = (overrides: Record<string, unknown>) => ({
  section: 'Lumen Studio S.r.l.',
  key: 'sales_register',
  label: 'Registro delle vendite',
  status: 'included',
  date_rule: 'per data di emissione',
  reason: null,
  cause: null,
  path: 'vendite/registro-vendite.csv',
  count: 12,
  unit: 'rows',
  ...overrides,
})

const stored = (overrides: Partial<StoredExport> = {}): StoredExport => ({
  id: 'e-1',
  legal_entity_id: 'le-1',
  scope: 'entity',
  period_from: '2026-07-01',
  period_to: '2026-09-30',
  built_by: null,
  built_at: '2026-10-12T09:40:00Z',
  expires_at: '2027-11-12T09:40:00Z',
  deleted_at: null,
  parts: [
    {
      part: 1,
      filename: 'commercialista-lumen.zip',
      size_bytes: 40_265_318,
      sha256: 'a'.repeat(64),
      download_url: '/api/accountant-export/exports/e-1/parts/1',
    },
  ],
  checklist: { parts: [line({})], missing: [] },
  ...overrides,
})

let exportsList: StoredExport[]
let partsAnswer: Promise<unknown> | unknown

beforeEach(() => {
  session.role = 'admin'
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 10, 10, 9, 0, 0))
  exportsList = []
  partsAnswer = PARTS
  vi.mocked(api.POST).mockReset()
  vi.mocked(downloadFromApi).mockClear()
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.GET).mockImplementation(((path: string, init?: { params?: { path?: { export_id?: string } } }) => {
    if (path === '/api/accountant-export/exports') {
      return ok({ items: exportsList, total_count: exportsList.length })
    }
    if (path === '/api/accountant-export/exports/{export_id}') {
      return ok(exportsList.find((item) => item.id === init?.params?.path?.export_id))
    }
    if (path === '/api/accountant-export/parts') {
      return partsAnswer instanceof Promise ? partsAnswer : ok(partsAnswer)
    }
    if (path === '/api/legal-entities/{legal_entity_id}/fiscal-profile') {
      return ok({ iva_frequency: 'monthly' })
    }
    if (path === '/api/users') return ok([{ id: 'u-2', name: 'Carla Neri' }])
    return ok(undefined)
  }) as never)
  window.matchMedia = ((query: string) => ({
    matches: /min-width/.test(query),
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia
})
afterEach(() => vi.useRealTimers())

function page({
  tab = 'prepare',
  item = null,
  legal = companies(),
  onTabChange = vi.fn(),
  onItemChange = vi.fn(),
}: {
  tab?: ExportTab
  item?: string | null
  legal?: LegalEntityValue
  onTabChange?: (next: ExportTab) => void
  onItemChange?: (next: string | null) => void
} = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <LegalEntityContext.Provider value={legal}>
        <ExportPage tab={tab} item={item} onTabChange={onTabChange} onItemChange={onItemChange} />
      </LegalEntityContext.Provider>
    </QueryClientProvider>,
  )
  return { onTabChange, onItemChange }
}

const posted = () =>
  vi.mocked(api.POST).mock.calls.map((call) => (call as unknown as [string, { body: unknown }])[1].body)

describe('«Prepara»', () => {
  it('lists what the server says a build holds, and nothing the page knows by itself', async () => {
    page()
    const list = await screen.findByRole('list', { name: 'Cosa contiene' })
    expect(within(list).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      'Registro delle venditeElenco CSV · per data di emissione',
      'Fatture emesse: file originaliFile originali · per data del documento',
    ])
    // No share link, Drive, payroll or checklist block: their own cards build them.
    expect(screen.queryByText(/Link condivisi|Drive|Parti paghe|Variazioni paghe|Il periodo è pronto/)).toBeNull()
  })

  it('names no company in a space that has one, and opens on the last quarter its IVA is settled by', async () => {
    vi.mocked(api.GET).mockImplementation(((path: string) =>
      path === '/api/legal-entities/{legal_entity_id}/fiscal-profile'
        ? ok({ iva_frequency: 'quarterly' })
        : path === '/api/accountant-export/parts'
          ? ok(PARTS)
          : ok({ items: [], total_count: 0 })) as never)
    page()
    expect(await screen.findByText('dal 01/07/2026 al 30/09/2026')).toBeInTheDocument()
    expect(screen.queryByText('Azienda')).toBeNull()
    expect(screen.getByRole('button', { name: 'Trimestre', pressed: true })).toBeInTheDocument()
  })

  it('opens on the last complete month when the company has no fiscal profile', async () => {
    vi.mocked(api.GET).mockImplementation(((path: string) =>
      path === '/api/legal-entities/{legal_entity_id}/fiscal-profile'
        ? refused(404, { detail: 'nope' })
        : path === '/api/accountant-export/parts'
          ? ok(PARTS)
          : ok({ items: [], total_count: 0 })) as never)
    page()
    expect(await screen.findByText('dal 01/10/2026 al 31/10/2026')).toBeInTheDocument()
  })

  it('asks for the build with the period written out, and moves to the list where it is made', async () => {
    vi.mocked(api.POST).mockReturnValue(new Promise(() => {}) as never)
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const { onTabChange } = page()
    await screen.findByText('dal 01/10/2026 al 31/10/2026')
    await user.click(screen.getByRole('button', { name: 'Crea esportazione' }))
    expect(posted()).toEqual([
      { date_from: '2026-10-01', date_to: '2026-10-31', legal_entity_id: 'le-1', all_entities: false },
    ])
    expect(onTabChange).toHaveBeenCalledWith('exports')
  })

  it('never preselects «Tutte le aziende», and asks for it by name with a band that says what it holds', async () => {
    vi.mocked(api.POST).mockReturnValue(new Promise(() => {}) as never)
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    page({ legal: SEVERAL })
    const field = await screen.findByRole('combobox', { name: 'Azienda' })
    expect(field).toHaveTextContent('Lumen Studio S.r.l.')
    expect(screen.queryByRole('note')).toBeNull()
    await user.click(field)
    expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual([
      'Lumen Studio S.r.l.',
      'Rebase Ltd',
      'Tutte le aziende',
    ])
    await user.click(screen.getByRole('option', { name: 'Tutte le aziende' }))
    expect(screen.getByRole('note')).toHaveTextContent('Comprende anche gli acquisti che non hanno un’azienda.')
    await user.click(screen.getByRole('button', { name: 'Crea esportazione' }))
    expect(posted()).toEqual([
      { date_from: '2026-10-01', date_to: '2026-10-31', legal_entity_id: null, all_entities: true },
    ])
  })

  it('starts from the company the sidebar has selected', async () => {
    page({ legal: { ...SEVERAL, selected: 'le-2' } })
    expect(await screen.findByRole('combobox', { name: 'Azienda' })).toHaveTextContent('Rebase Ltd')
  })

  it('offers a scoped admin only their companies, and a pinned one as a label', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    page({ legal: { ...SEVERAL, scoped: true } })
    await user.click(await screen.findByRole('combobox', { name: 'Azienda' }))
    expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual([
      'Lumen Studio S.r.l.',
      'Rebase Ltd',
    ])
  })

  it('draws a pinned company as a label with no control', async () => {
    page({ legal: companies({ pinned: true, scoped: true }) })
    await screen.findByText('Azienda')
    expect(screen.getByText('Lumen Studio S.r.l.')).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Azienda' })).toBeNull()
  })

  it('shows a parts list that could not be read as the error and keeps the button, since a build reads the period itself', async () => {
    partsAnswer = refused(500, { detail: 'Non riesco a leggere le parti.' })
    page()
    expect(await screen.findByRole('alert')).toHaveTextContent('Non riesco a leggere le parti.')
    expect(screen.getByRole('button', { name: 'Crea esportazione' })).toBeEnabled()
  })

  it('waits with a sentence while the parts are read', async () => {
    partsAnswer = new Promise(() => {})
    page()
    expect(await screen.findByRole('status', { name: 'Controllo il periodo…' })).toBeInTheDocument()
  })

  it('leaves the button off for a role that may not build, which the entry never shows', async () => {
    session.role = 'collaborator'
    page()
    expect(await screen.findByRole('button', { name: 'Crea esportazione' })).toBeDisabled()
  })
})

describe('«Esportazioni»', () => {
  it('says there is nothing, in one sentence and one action, with no illustration', async () => {
    const { onTabChange } = page({ tab: 'exports' })
    expect(await screen.findByText('Nessuna esportazione')).toBeInTheDocument()
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await user.click(screen.getByRole('button', { name: 'Prepara un’esportazione' }))
    expect(onTabChange).toHaveBeenCalledWith('prepare')
  })

  it('waits with a sentence while the exports are read', async () => {
    vi.mocked(api.GET).mockReturnValue(new Promise(() => {}) as never)
    page({ tab: 'exports' })
    expect(await screen.findByRole('status', { name: 'Leggo le esportazioni…' })).toBeInTheDocument()
  })

  it('shows what the server refuses as the banner and nothing under it', async () => {
    vi.mocked(api.GET).mockImplementation((() =>
      refused(403, {
        code: 'permission_denied',
        detail: 'scaricare requires one of [admin], actor has collaborator',
        actual_role: 'collaborator',
        required_roles: ['admin'],
      })) as never)
    page({ tab: 'exports' })
    expect(await screen.findByRole('alert')).toHaveTextContent(/collaboratore/)
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('lists a complete export, one with open points in gold and a deleted one as a muted row', async () => {
    exportsList = [
      stored({ id: 'e-3', checklist: { parts: [line({})], missing: [{ section: 's', document: '2026/1', kind: 'XML', reason: 'x' }] } }),
      stored({ id: 'e-2' }),
      stored({ id: 'e-1', deleted_at: '2026-11-10T03:30:00Z', parts: [] }),
    ]
    page({ tab: 'exports' })
    const rows = await screen.findAllByRole('row')
    expect(rows).toHaveLength(4)
    const [open, complete, deleted] = rows.slice(1)
    expect(within(open as HTMLElement).getByText('1 aperti').closest('[data-tone]')).toHaveAttribute('data-tone', 'gold')
    expect(within(complete as HTMLElement).getByText('Completa').closest('[data-tone]')).toHaveAttribute('data-tone', 'ink')
    expect(within(deleted as HTMLElement).getByText('Eliminata').closest('[data-tone]')).toHaveAttribute('data-tone', 'muted')
    expect(within(deleted as HTMLElement).getByText('Oltre i 13 mesi')).toBeInTheDocument()
    expect(within(deleted as HTMLElement).queryByText('Completa')).toBeNull()
    expect(within(complete as HTMLElement).getByText('3° trimestre 2026 · 1 parte')).toBeInTheDocument()
    expect(await screen.findByRole('tab', { name: /Esportazioni.*3/ })).toBeInTheDocument()
  })

  it('names a company of sixty characters whole on hover and ellipsises it in the cell', async () => {
    const name = 'Rebase Ltd con un nome molto lungo per provare le liste ok'
    const long = { id: 'le-9', name, active: true } as unknown as LegalEntityValue['legal_entities'][number]
    exportsList = [stored({ legal_entity_id: 'le-9' })]
    page({ tab: 'exports', legal: companies({ legal_entities: [long], byId: () => long }) })
    const cell = await screen.findByText(name)
    expect(cell).toHaveAttribute('title', name)
    expect(cell).toHaveClass('truncate')
  })

  it('names an export of every company by «Tutte le aziende»', async () => {
    exportsList = [stored({ scope: 'all_entities', legal_entity_id: null })]
    page({ tab: 'exports' })
    expect(await screen.findByText('Tutte le aziende')).toBeInTheDocument()
  })

  it('asks for the next fifty with «Mostra altri» and says how many there are in all', async () => {
    exportsList = Array.from({ length: 3 }, (_, i) => stored({ id: `e-${i}` }))
    vi.mocked(api.GET).mockImplementation(((path: string, init?: { params?: { query?: { offset?: number } } }) => {
      if (path !== '/api/accountant-export/exports') return ok(undefined)
      const offset = init?.params?.query?.offset ?? 0
      return ok({ items: exportsList.slice(offset, offset + 2), total_count: 120 })
    }) as never)
    page({ tab: 'exports' })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    expect(await screen.findByText('Mostrate 2 di 120 esportazioni.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Mostra altri' }))
    await waitFor(() => expect(screen.getByText('Mostrate 3 di 120 esportazioni.')).toBeInTheDocument())
    const offsets = vi
      .mocked(api.GET)
      .mock.calls.map((call) => (call as unknown as [string, { params: { query: { offset?: number; limit?: number } } }]))
      .filter(([path, init]) => path === '/api/accountant-export/exports' && init.params.query.limit === 50)
      .map(([, init]) => init.params.query.offset)
    expect(offsets).toEqual([0, 2])
  })

  it('filters by year, and by company only when the space has several', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    page({ tab: 'exports' })
    await screen.findByRole('combobox', { name: 'Anno' })
    expect(screen.queryByRole('combobox', { name: 'Azienda' })).toBeNull()
    await user.click(screen.getByRole('combobox', { name: 'Anno' }))
    await user.click(screen.getByRole('option', { name: '2025' }))
    await waitFor(() => {
      const years = vi
        .mocked(api.GET)
        .mock.calls.map((call) => (call as unknown as [string, { params: { query: { year?: number } } }]))
        .filter(([path]) => path === '/api/accountant-export/exports')
        .map(([, init]) => init.params.query.year)
      expect(years).toContain(2025)
    })
  })

  it('offers the company filter in a space with several', async () => {
    page({ tab: 'exports', legal: SEVERAL })
    expect(await screen.findByRole('combobox', { name: 'Azienda' })).toBeInTheDocument()
  })
})

describe('the open export', () => {
  it('shows the index, the check as it was made and one ZIP with its size as the strong button', async () => {
    exportsList = [
      stored({
        checklist: {
          parts: [
            line({}),
            line({ key: 'issued_invoice_files', label: 'Fatture emesse: file originali', path: 'fatture-emesse/', count: 4, unit: 'files' }),
            line({ key: 'received_invoice_files', label: 'Fatture ricevute: file originali', status: 'omitted', cause: 'module', reason: 'il modulo «purchases» è spento', path: null, count: null, unit: null }),
            line({ key: 'sales_vat', label: 'Registro IVA delle vendite', status: 'omitted', cause: 'pack', reason: 'non-it', path: null, count: null, unit: null }),
            line({ key: 'bank_movements', label: 'Movimenti di banca', status: 'unavailable', reason: 'la fonte non è ancora disponibile in PigroCRM', path: null, count: null, unit: null }),
          ],
          missing: Array.from({ length: 7 }, (_, i) => ({ section: 's', document: `2026/${i + 1}`, kind: 'XML', reason: 'il file non è archiviato' })),
        },
      }),
    ]
    page({ tab: 'exports', item: 'e-1' })
    const panel = await screen.findByRole('complementary', { name: 'Esportazione aperta' })
    await within(panel).findByText('Cosa c’è nello ZIP')
    expect(within(panel).getByText('12 righe')).toBeInTheDocument()
    expect(within(panel).getByText('4 file')).toBeInTheDocument()
    // A part the pack left out is named; one a module left out, and one not built, are not.
    expect(within(panel).getByText('Registro IVA delle vendite')).toBeInTheDocument()
    expect(within(panel).queryByText(/Fatture ricevute|purchases|Movimenti di banca|modulo/)).toBeNull()
    // Seven documents: five by name and the rest counted.
    expect(within(panel).getByText('7 documenti non sono stati allegati.')).toBeInTheDocument()
    expect(within(panel).getByText('2026/5 · XML')).toBeInTheDocument()
    expect(within(panel).queryByText('2026/6 · XML')).toBeNull()
    expect(within(panel).getByText(/e altre 2/)).toBeInTheDocument()
    const download = within(panel).getByRole('button', { name: /Scarica lo ZIP/ })
    expect(download).toHaveTextContent('38,4 MiB')
    expect(within(panel).getByText(/Si conserva fino al 12\/11\/2027, poi viene eliminata e ne resta traccia/)).toBeInTheDocument()
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await user.click(download)
    expect(downloadFromApi).toHaveBeenCalledWith('/api/accountant-export/exports/e-1/parts/1')
    // The delivery that does not exist yet is not drawn.
    expect(within(panel).queryByText(/link|Drive/i)).toBeNull()
  })

  it('offers each file of a split export and says how many there are', async () => {
    const part = (n: number, size: number) => ({
      part: n,
      filename: `p${n}.zip`,
      size_bytes: size,
      sha256: 'b'.repeat(64),
      download_url: `/api/accountant-export/exports/e-1/parts/${n}`,
    })
    exportsList = [stored({ parts: [part(1, 90_000_000), part(2, 12_000_000), part(3, 1_024)] })]
    page({ tab: 'exports', item: 'e-1' })
    const panel = await screen.findByRole('complementary', { name: 'Esportazione aperta' })
    expect(await within(panel).findByText(/3 file ZIP/)).toBeInTheDocument()
    const buttons = within(panel).getAllByRole('button', { name: /Scarica il file/ })
    expect(buttons.map((button) => button.textContent)).toEqual([
      'Scarica il file 1 di 385,8 MiB',
      'Scarica il file 2 di 311,4 MiB',
      'Scarica il file 3 di 31,0 KiB',
    ])
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await user.click(buttons[1] as HTMLElement)
    expect(downloadFromApi).toHaveBeenCalledWith('/api/accountant-export/exports/e-1/parts/2')
  })

  it('names who made it: a colleague by name, the space for a job, you for yourself', async () => {
    exportsList = [stored({ built_by: 'u-2' })]
    page({ tab: 'exports', item: 'e-1' })
    expect(await screen.findByText('Creata il 12/10/2026 11:40 da Carla Neri')).toBeInTheDocument()
  })

  it('keeps an export past its 13 months as a trace: no delivery, no download', async () => {
    exportsList = [stored({ deleted_at: '2026-11-10T03:30:00Z', parts: [] })]
    page({ tab: 'exports', item: 'e-1' })
    const panel = await screen.findByRole('complementary', { name: 'Esportazione aperta' })
    expect(await within(panel).findByText(/Eliminata il 10\/11\/2026: oltre i 13 mesi/)).toBeInTheDocument()
    expect(within(panel).queryByRole('button', { name: /Scarica/ })).toBeNull()
    expect(within(panel).queryByText(/Si conserva/)).toBeNull()
  })

  it('shows a link to an export that cannot be read as the server’s sentence, with a way out', async () => {
    vi.mocked(api.GET).mockImplementation(((path: string) =>
      path === '/api/accountant-export/exports/{export_id}'
        ? refused(404, { detail: 'Esportazione non trovata.' })
        : ok({ items: [], total_count: 0 })) as never)
    const { onItemChange } = page({ tab: 'exports', item: 'e-1' })
    const panel = await screen.findByRole('complementary', { name: 'Esportazione aperta' })
    expect(await within(panel).findByText('Esportazione non trovata.')).toBeInTheDocument()
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await user.click(within(panel).getAllByRole('button', { name: 'Chiudi' })[0] as HTMLElement)
    expect(onItemChange).toHaveBeenCalledWith(null)
  })
})

/** The page with its own tab and open export, as the route holds them, so a build can be followed
 *  from the form to the list. */
function Harness({ legal }: { legal: LegalEntityValue }) {
  const [tab, setTab] = useState<ExportTab>('prepare')
  const [item, setItem] = useState<string | null>(null)
  return (
    <LegalEntityContext.Provider value={legal}>
      <ExportPage tab={tab} item={item} onTabChange={setTab} onItemChange={setItem} />
    </LegalEntityContext.Provider>
  )
}

function followed() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <Harness legal={companies()} />
    </QueryClientProvider>,
  )
}

async function ask(user: UserEvent) {
  await screen.findByText('dal 01/10/2026 al 31/10/2026')
  await user.click(screen.getByRole('button', { name: 'Crea esportazione' }))
}

describe('a build in progress and one that failed', () => {
  it('is a row with «In preparazione» and the sentence, and opens the export once it is made', async () => {
    // `Promise.withResolvers` is not in this project's TypeScript lib.
    let resolve: (value: unknown) => void = () => {}
    const promise = new Promise<unknown>((done) => {
      resolve = done
    })
    vi.mocked(api.POST).mockReturnValue(promise as never)
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    followed()
    await ask(user)
    const row = (await screen.findByText('In preparazione')).closest('tr') as HTMLElement
    expect(within(row).getByText('Lumen Studio S.r.l.')).toBeInTheDocument()
    expect(within(row).getByRole('status')).toHaveTextContent('Preparo l’esportazione…')
    exportsList = [stored({ id: 'e-new' })]
    resolve({ data: stored({ id: 'e-new' }), response: new Response(null, { status: 201 }) })
    await waitFor(() => expect(screen.queryByText('In preparazione')).toBeNull())
    expect(await screen.findByRole('complementary', { name: 'Esportazione aperta' })).toBeInTheDocument()
  })

  it('shows the reason in danger with «Riprova», which asks again for the same build', async () => {
    vi.mocked(api.POST)
      .mockReturnValueOnce(refused(500, { code: 'internal', detail: 'Il disco del server è pieno.' }) as never)
      .mockReturnValueOnce(new Promise(() => {}) as never)
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    followed()
    await ask(user)
    const failed = (await screen.findByText('Non riuscita')).closest('tr') as HTMLElement
    expect(within(failed).getByRole('alert')).toHaveTextContent('Il disco del server è pieno.')
    expect(within(failed).getByText('Non riuscita').closest('[data-tone]')).toHaveAttribute('data-tone', 'danger')
    await user.click(within(failed).getByRole('button', { name: 'Riprova' }))
    expect(await screen.findByText('In preparazione')).toBeInTheDocument()
    expect(screen.queryByText('Non riuscita')).toBeNull()
    expect(posted()).toHaveLength(2)
    expect(posted()[1]).toEqual(posted()[0])
  })

  it('lets a refusal be closed', async () => {
    vi.mocked(api.POST).mockReturnValueOnce(refused(422, { code: 'validation_failed', detail: 'No.' }) as never)
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    followed()
    await ask(user)
    await user.click(await screen.findByRole('button', { name: 'Chiudi' }))
    expect(screen.queryByText('Non riuscita')).toBeNull()
  })
})
