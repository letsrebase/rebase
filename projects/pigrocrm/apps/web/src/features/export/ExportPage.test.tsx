/**
 * «Esporta per il commercialista» (REB-1028, the approved brief of 2026-11-10): the form that
 * asks for a build, the list of stored exports and the panel that downloads one, and the rules
 * the brief gives a component that it must not break: «Tutte le aziende» never preselected, no
 * part or sentence for a module that is off, an admin's page; and, from REB-1047, the delivery: the
 * share link (made once and shown once, never copied from a list, revoked on one click), the copy
 * in Drive (only where the storage is Drive) and the tab «Link condivisi»; and, from REB-1048, the
 * readiness checklist before «Crea esportazione», the «Tipo» «Variazioni paghe» with the pay parts,
 * and the link with a code for an export that carries pay.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent, { type UserEvent } from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { toast } from '@rebase/ui/sonner'
import { downloadFromApi } from '@/lib/download'
import { LegalEntityContext, type LegalEntityValue } from '@/lib/legalEntity'
import { ExportPage } from './ExportPage'
import type { DriveStatus, DriveWrite, ShareLink, StoredExport } from './labels'
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
// The checklist links to the places that fix each point; the page test has no router to draw them.
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => <a href={to}>{children}</a>,
}))

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
  { key: 'sales_register', label: 'Registro delle vendite', date_rule: 'per data di emissione', contents: 'Elenco CSV', opt_in: null },
  { key: 'issued_invoice_files', label: 'Fatture emesse: file originali', date_rule: 'per data del documento', contents: 'File originali', opt_in: null },
]
const PAYSLIPS = { key: 'payslips', label: 'Cedolini: elenco e file originali', date_rule: 'per mese di competenza', contents: 'Elenco CSV e file originali', opt_in: 'payslips' }
const CHANGES_PARTS = [
  { key: 'staff_hires', label: 'Assunzioni', date_rule: 'per data di inizio', contents: 'Elenco CSV', opt_in: null },
  { key: 'staff_expense_notes', label: 'Note spese da rimborsare in busta paga', date_rule: 'per mese della busta paga', contents: 'Elenco CSV', opt_in: null },
]

const check = (overrides: Record<string, unknown>) => ({
  key: 'inbox',
  label: 'Documenti in arrivo ancora da guardare',
  state: 'ok',
  count: 0,
  items: [],
  more: 0,
  target: 'inbox',
  ...overrides,
})
const MOVEMENT = check({
  key: 'bank_movements',
  label: 'Movimenti di banca da abbinare',
  state: 'open',
  count: 7,
  items: ['03/10/2026 Fornitore Rossi', '04/10/2026 Fornitore Bianchi'],
  more: 5,
  target: 'cash',
})
const PAYSLIP_CHECK = check({
  key: 'payroll',
  label: 'Cedolini del periodo',
  state: 'open',
  count: 1,
  items: ['Carla Rossi: cedolino di ottobre 2026 da accettare'],
  target: 'staff',
})
const checklistOf = (...checks: Record<string, unknown>[]) => ({
  period_from: '2026-10-01',
  period_to: '2026-10-31',
  open_count: checks.reduce((sum, item) => sum + (item.count as number), 0),
  checks,
})

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
  kind: 'accountant',
  payroll: false,
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

const link = (overrides: Partial<ShareLink> = {}): ShareLink => ({
  id: 'l-1',
  export_id: 'e-1',
  legal_entity_id: 'le-1',
  scope: 'entity',
  created_at: '2026-10-13T08:00:00Z',
  created_by: 'u-2',
  expires_at: '2026-10-20T08:00:00Z',
  revoked_at: null,
  state: 'active',
  recipient_email: null,
  download_count: 0,
  period_from: '2026-07-01',
  period_to: '2026-09-30',
  openings: [],
  ...overrides,
})

let exportsList: StoredExport[]
let partsAnswer: Promise<unknown> | unknown
let linksList: ShareLink[]
let driveStatus: DriveStatus
let driveWrites: DriveWrite[]
let checklistAnswer: unknown
let staffOn: boolean

beforeEach(() => {
  session.role = 'admin'
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 10, 10, 9, 0, 0))
  exportsList = []
  partsAnswer = PARTS
  linksList = []
  driveStatus = { available: false, reason: 'local_storage' }
  driveWrites = []
  checklistAnswer = checklistOf(check({}))
  staffOn = false
  vi.mocked(toast.success).mockClear()
  vi.mocked(toast.error).mockClear()
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
    if (path === '/api/accountant-export/checklist') {
      return checklistAnswer instanceof Promise ? checklistAnswer : ok(checklistAnswer)
    }
    if (path === '/api/settings/modules') {
      return ok({
        profile: 'company',
        onboarding: 'done',
        modules: [{ key: 'staff', label: 'Persone', enabled: staffOn }],
      })
    }
    if (path === '/api/accountant-export/share-links') {
      return ok({ items: linksList, total_count: linksList.length })
    }
    if (path === '/api/accountant-export/share-links/{link_id}') {
      return ok(linksList.find((item) => item.id === (init as { params?: { path?: { link_id?: string } } })?.params?.path?.link_id))
    }
    if (path === '/api/accountant-export/drive') return ok(driveStatus)
    if (path === '/api/accountant-export/exports/{export_id}/drive-writes') return ok(driveWrites)
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
  openLink = null,
  legal = companies(),
  onTabChange = vi.fn(),
  onItemChange = vi.fn(),
  onLinkChange = vi.fn(),
  onOpenLink = vi.fn(),
  onOpenExport = vi.fn(),
}: {
  tab?: ExportTab
  item?: string | null
  openLink?: string | null
  legal?: LegalEntityValue
  onTabChange?: (next: ExportTab) => void
  onItemChange?: (next: string | null) => void
  onLinkChange?: (next: string | null) => void
  onOpenLink?: (id: string) => void
  onOpenExport?: (id: string) => void
} = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <LegalEntityContext.Provider value={legal}>
        <ExportPage
          tab={tab}
          item={item}
          link={openLink}
          onTabChange={onTabChange}
          onItemChange={onItemChange}
          onLinkChange={onLinkChange}
          onOpenLink={onOpenLink}
          onOpenExport={onOpenExport}
        />
      </LegalEntityContext.Provider>
    </QueryClientProvider>,
  )
  return { onTabChange, onItemChange, onLinkChange, onOpenLink, onOpenExport }
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
    // No pay block while the space has no staff, and no delivery in the form: delivery is done on a
    // stored export (brief, answer 1).
    expect(screen.queryByText(/Drive|Parti paghe|Variazioni paghe|Crea un link/)).toBeNull()
    expect(screen.getByRole('tab', { name: 'Link condivisi' })).toBeInTheDocument()
  })

  it('names no company in a space that has one, and opens on the last quarter its IVA is settled by', async () => {
    vi.mocked(api.GET).mockImplementation(((path: string) =>
      path === '/api/legal-entities/{legal_entity_id}/fiscal-profile'
        ? ok({ iva_frequency: 'quarterly' })
        : path === '/api/accountant-export/parts'
          ? ok(PARTS)
          : path === '/api/accountant-export/checklist'
            ? ok(checklistOf(check({})))
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
          : path === '/api/accountant-export/checklist'
            ? ok(checklistOf(check({})))
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
      {
        date_from: '2026-10-01',
        date_to: '2026-10-31',
        legal_entity_id: 'le-1',
        all_entities: false,
        kind: 'accountant',
        payroll_parts: [],
      },
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
      {
        date_from: '2026-10-01',
        date_to: '2026-10-31',
        legal_entity_id: null,
        all_entities: true,
        kind: 'accountant',
        payroll_parts: [],
      },
    ])
  })

  it('starts from the company the sidebar has selected', async () => {
    page({ legal: { ...SEVERAL, selected: 'le-2' } })
    expect(await screen.findByRole('combobox', { name: 'Azienda' })).toHaveTextContent('Rebase Ltd')
  })

  it('offers a scoped admin only their companies, with no «Tutte le aziende»', async () => {
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

  it('gives a role that may not build no form, only the server’s sentence, which the entry never leads to', async () => {
    session.role = 'collaborator'
    partsAnswer = refused(403, {
      code: 'permission_denied',
      detail: 'x',
      actual_role: 'collaborator',
      required_roles: ['admin'],
    })
    page()
    expect(await screen.findByRole('alert')).toHaveTextContent(/collaboratore/)
    expect(screen.queryByRole('button', { name: 'Crea esportazione' })).toBeNull()
  })

  it('waits for the IVA frequency before it lets the build go, since the period depends on it', async () => {
    vi.mocked(api.GET).mockImplementation(((path: string) =>
      path === '/api/legal-entities/{legal_entity_id}/fiscal-profile'
        ? new Promise(() => {})
        : path === '/api/accountant-export/parts'
          ? ok(PARTS)
          : path === '/api/accountant-export/checklist'
            ? ok(checklistOf(check({})))
            : ok({ items: [], total_count: 0 })) as never)
    page()
    await screen.findByText('Registro delle vendite')
    expect(screen.getByRole('button', { name: 'Crea esportazione' })).toBeDisabled()
  })

  it('asks for the build once however many times the button is pressed', async () => {
    vi.mocked(api.POST).mockReturnValue(new Promise(() => {}) as never)
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    page()
    await screen.findByText('dal 01/10/2026 al 31/10/2026')
    await user.dblClick(screen.getByRole('button', { name: 'Crea esportazione' }))
    expect(posted()).toHaveLength(1)
  })
})

describe('«Il periodo è pronto?»', () => {
  it('draws every check as a row, the fine ones included, and links each open one to where it is fixed', async () => {
    checklistAnswer = checklistOf(check({}), MOVEMENT)
    page()
    const list = await screen.findByRole('list', { name: 'Il periodo è pronto?' })
    const rows = within(list).getAllByRole('listitem', { name: '' }).filter((row) => row.dataset.check)
    expect(rows.map((row) => row.dataset.check)).toEqual(['inbox', 'bank_movements'])
    expect(within(rows[0]).getByText('A posto')).toBeInTheDocument()
    expect(within(rows[1]).getByText('Da vedere · 7')).toBeInTheDocument()
    // The first points by name and the rest counted, never listed.
    expect(within(rows[1]).getByText('03/10/2026 Fornitore Rossi')).toBeInTheDocument()
    expect(within(rows[1]).getByText('e altre 5')).toBeInTheDocument()
    expect(within(rows[1]).getByRole('link', { name: 'Apri Cassa' })).toHaveAttribute('href', '/app/cash')
    // A check with nothing to fix has no link.
    expect(within(rows[0]).queryByRole('link')).toBeNull()
  })

  it('says open points do not stop the export, and keeps the button pressable', async () => {
    checklistAnswer = checklistOf(MOVEMENT)
    page()
    expect(await screen.findByText(/Ci sono 7 punti da vedere\./)).toHaveTextContent(
      'Non fermano l’esportazione',
    )
    expect(screen.getByRole('button', { name: 'Crea esportazione' })).toBeEnabled()
  })

  it('reads the period again for the company and the period chosen', async () => {
    page({ legal: SEVERAL })
    await screen.findByRole('list', { name: 'Il periodo è pronto?' })
    const asked = vi
      .mocked(api.GET)
      .mock.calls.filter((call) => call[0] === '/api/accountant-export/checklist')
      .map((call) => (call[1] as { params: { query: unknown } }).params.query)
    expect(asked[0]).toEqual({
      date_from: '2026-10-01',
      date_to: '2026-10-31',
      legal_entity_id: 'le-1',
      all_entities: undefined,
      payroll_parts: undefined,
    })
  })

  it('shows a checklist that could not be read as the banner, never as «tutto a posto», and keeps the button', async () => {
    checklistAnswer = refused(500, { detail: 'Non riesco a leggere il periodo.' })
    page()
    expect(await screen.findByText('Non riesco a leggere il periodo.')).toBeInTheDocument()
    expect(screen.queryByText('A posto')).toBeNull()
    expect(screen.getByRole('button', { name: 'Crea esportazione' })).toBeEnabled()
  })

  it('waits with a sentence while the period is checked', async () => {
    checklistAnswer = new Promise(() => {})
    page()
    expect((await screen.findAllByText('Controllo il periodo…')).length).toBeGreaterThan(0)
    expect(screen.queryByText('A posto')).toBeNull()
  })
})

describe('«Variazioni paghe» and the pay parts', () => {
  beforeEach(() => {
    staffOn = true
    partsAnswer = [...PARTS, PAYSLIPS]
  })

  it('offers the pay parts as checkboxes off by default, with the sentence that they never go on a plain link', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    page()
    const box = await screen.findByRole('checkbox', { name: 'Cedolini' })
    expect(box).not.toBeChecked()
    expect(screen.getByText(/non viaggiano mai su un link semplice/)).toBeInTheDocument()
    // The part that is asked for is not in «Cosa contiene».
    const list = screen.getByRole('list', { name: 'Cosa contiene' })
    expect(within(list).queryByText(/Cedolini/)).toBeNull()
    // Ticking it asks the checklist for the payroll check, and the build for the part.
    checklistAnswer = checklistOf(check({}), PAYSLIP_CHECK)
    vi.mocked(api.POST).mockReturnValue(new Promise(() => {}) as never)
    await user.click(box)
    expect(await screen.findByText('Cedolini del periodo')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Crea esportazione' }))
    expect(posted()).toEqual([expect.objectContaining({ kind: 'accountant', payroll_parts: ['payslips'] })])
  })

  it('draws «Tipo» only for a space with staff and builds «Variazioni paghe» for a month, with no checklist', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    vi.mocked(api.POST).mockReturnValue(new Promise(() => {}) as never)
    page()
    await user.click(await screen.findByRole('button', { name: 'Variazioni paghe' }))
    expect(screen.queryByRole('list', { name: 'Il periodo è pronto?' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Trimestre' })).toBeNull()
    expect(screen.queryByRole('checkbox', { name: 'Cedolini' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Crea esportazione' }))
    expect(posted()).toEqual([expect.objectContaining({ kind: 'payroll_changes', payroll_parts: [] })])
  })

  it('has no «Tipo» and no pay block while the staff module is off, and names no module', async () => {
    staffOn = false
    partsAnswer = PARTS
    page()
    await screen.findByRole('list', { name: 'Cosa contiene' })
    expect(screen.queryByRole('button', { name: 'Variazioni paghe' })).toBeNull()
    expect(screen.queryByText(/Parti paghe/)).toBeNull()
  })

  it('tells the two kinds apart in the list with a chip, and shows the checklist an export was made with', async () => {
    exportsList = [
      stored({
        id: 'e-5',
        checklist: {
          parts: [line({})],
          missing: [],
          readiness: checklistOf(check({}), MOVEMENT),
        },
      }),
      stored({
        id: 'e-6',
        kind: 'payroll_changes',
        payroll: true,
        checklist: { parts: [line({ key: 'staff_hires', label: 'Assunzioni', path: 'assunzioni/elenco.csv', count: 1 })], missing: [] },
      }),
    ]
    page({ tab: 'exports', item: 'e-5' })
    expect(await screen.findAllByText('Variazioni paghe')).not.toHaveLength(0)
    expect(screen.getAllByText('Commercialista').length).toBeGreaterThan(0)
    // The check of the first reads «7 aperti» from the snapshot, the payroll file wears none.
    expect((await screen.findAllByText('7 aperti')).length).toBeGreaterThan(0)
    const panel = await screen.findByRole('complementary', { name: 'Esportazione aperta' })
    const snapshot = await within(panel).findByRole('list', { name: 'Il periodo com’era alla creazione' })
    expect(within(snapshot).getByText('Da vedere · 7')).toBeInTheDocument()
    // A snapshot says how the period stood: no link to fix it now.
    expect(within(snapshot).queryByRole('link')).toBeNull()
  })

  it('offers an export with pay only a link with a code, and never submits it without a recipient', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    exportsList = [
      stored({
        kind: 'payroll_changes',
        payroll: true,
        checklist: { parts: [line({ key: 'staff_hires', label: 'Assunzioni', path: 'assunzioni/elenco.csv', count: 1 })], missing: [] },
      }),
    ]
    vi.mocked(api.POST).mockImplementation(((path: string, init: { body: unknown }) =>
      path === '/api/accountant-export/exports/{export_id}/share-links'
        ? ok({
            ...link({ recipient_email: 'paghe@studio-esempio.it' }),
            url: 'https://pigro.example/app/shared-export?t=abc',
            body: init.body,
          })
        : ok(undefined)) as never)
    page({ tab: 'exports', item: 'e-1' })
    await user.click(await screen.findByRole('button', { name: 'Crea un link con codice' }))
    const dialog = await screen.findByRole('dialog', { name: 'Link con codice' })
    const submit = within(dialog).getByRole('button', { name: 'Crea il link con codice' })
    expect(submit).toBeDisabled()
    expect(within(dialog).queryByRole('button', { name: 'Crea il link' })).toBeNull()
    await user.type(within(dialog).getByLabelText('Destinatario'), 'paghe@studio-esempio.it')
    await user.click(submit)
    expect(posted()).toEqual([{ days: 7, recipient_email: 'paghe@studio-esempio.it' }])
    expect(await within(dialog).findByText(/Il codice va a paghe@studio-esempio.it/)).toBeInTheDocument()
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
    expect(within(open as HTMLElement).getByText('1 aperto').closest('[data-tone]')).toHaveAttribute('data-tone', 'gold')
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

  it('keeps the rows when a later page fails, and says so under them', async () => {
    exportsList = Array.from({ length: 2 }, (_, i) => stored({ id: `e-${i}` }))
    vi.mocked(api.GET).mockImplementation(((path: string, init?: { params?: { query?: { offset?: number } } }) => {
      if (path !== '/api/accountant-export/exports') return ok(undefined)
      const offset = init?.params?.query?.offset ?? 0
      return offset === 0
        ? ok({ items: exportsList, total_count: 120 })
        : refused(500, { code: 'internal', detail: 'Non riesco a leggere le altre.' })
    }) as never)
    page({ tab: 'exports' })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await user.click(await screen.findByRole('button', { name: 'Mostra altri' }))
    expect(await screen.findByText('Non riesco a leggere le altre.')).toBeInTheDocument()
    expect(screen.getAllByRole('row')).toHaveLength(3)
  })

  it('names no company in the empty sentence of a space that has one', async () => {
    page({ tab: 'exports' })
    expect(await screen.findByText(/scegli il periodo\./)).toBeInTheDocument()
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
  it('shows the index, the check as it was made and one ZIP with its size', async () => {
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
    // The link is how the file reaches the accountant: the panel's strong button.
    expect(within(panel).getByRole('button', { name: 'Crea un link per il commercialista' })).toBeInTheDocument()
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

  it.each([
    ['u-2', 'da Carla Neri'],
    ['u-1', 'da te'],
    [null, 'dallo spazio'],
  ])('names who made it (%s) as a person, you or the space', async (builtBy, tail) => {
    exportsList = [stored({ built_by: builtBy })]
    page({ tab: 'exports', item: 'e-1' })
    const panel = await screen.findByRole('complementary', { name: 'Esportazione aperta' })
    // The time is the reader's own zone: only the sentence's tail is the claim here.
    expect(await within(panel).findByText(/Creata il /)).toHaveTextContent(new RegExp(`${tail}$`))
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

  it('does not open a finished build over the form or over another export', async () => {
    let resolve: (value: unknown) => void = () => {}
    vi.mocked(api.POST).mockReturnValue(
      new Promise<unknown>((done) => {
        resolve = done
      }) as never,
    )
    exportsList = [stored({ id: 'e-new' }), stored({ id: 'e-other' })]
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    followed()
    await ask(user)
    await screen.findByText('In preparazione')
    // The admin opens another export while it is made: the new one does not take its place.
    await user.click((await screen.findAllByText('3° trimestre 2026 · 1 parte'))[1] as HTMLElement)
    expect(await screen.findByRole('complementary', { name: 'Esportazione aperta' })).toBeInTheDocument()
    resolve({ data: stored({ id: 'e-new' }), response: new Response(null, { status: 201 }) })
    await waitFor(() => expect(screen.queryByText('In preparazione')).toBeNull())
    const calls = vi.mocked(api.GET).mock.calls.map((call) => call as unknown as [string, { params?: { path?: { export_id?: string } } }])
    const opened = calls.filter(([path]) => path === '/api/accountant-export/exports/{export_id}').map(([, init]) => init.params?.path?.export_id)
    expect(opened).not.toContain('e-new')
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

describe('handing an export over: the share link', () => {
  const CREATED = { ...link({ id: 'l-new' }), url: 'https://pigro.example/studio/app/shared-export?t=abc' }

  async function openPanel() {
    exportsList = [stored()]
    page({ tab: 'exports', item: 'e-1' })
    return screen.findByRole('complementary', { name: 'Esportazione aperta' })
  }

  it('lists the links of the export with state, expiry, openings and «Revoca», and never a way to copy one', async () => {
    linksList = [
      link({ id: 'l-a', download_count: 3 }),
      link({ id: 'l-b', state: 'expired', expires_at: '2026-10-01T08:00:00Z' }),
      link({ id: 'l-c', state: 'revoked', revoked_at: '2026-10-14T08:00:00Z', download_count: 1 }),
    ]
    const panel = await openPanel()
    const list = await within(panel).findByRole('list', { name: 'Link dell’esportazione' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows.map((row) => within(row).getByText(/Attivo|Scaduto|Revocato/).textContent)).toEqual(['Attivo', 'Scaduto', 'Revocato'])
    expect(within(rows[0] as HTMLElement).getByText('3 aperture')).toBeInTheDocument()
    expect(within(rows[0] as HTMLElement).getByText(/scade il 20\/10\/2026/)).toBeInTheDocument()
    // Only a live link can be revoked; ended ones are muted rows.
    expect(within(list).getAllByRole('button', { name: 'Revoca' })).toHaveLength(1)
    expect(within(rows[1] as HTMLElement).getByText('Scaduto').closest('[data-tone]')).toHaveAttribute('data-tone', 'muted')
    expect(within(rows[2] as HTMLElement).getByText('Revocato').closest('[data-tone]')).toHaveAttribute('data-tone', 'muted')
    expect(within(panel).queryByRole('button', { name: /Copia/ })).toBeNull()
    expect(within(panel).queryByText(/shared-export/)).toBeNull()
  })

  it('says there is no link yet', async () => {
    const panel = await openPanel()
    expect(await within(panel).findByText('Nessun link per questa esportazione.')).toBeInTheDocument()
  })

  it('makes a link for a week by default, offers one to thirty days and shows the address once', async () => {
    vi.mocked(api.POST).mockReturnValue(ok(CREATED) as never)
    const writeText = vi.fn(() => Promise.resolve())
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const panel = await openPanel()
    await user.click(await within(panel).findByRole('button', { name: 'Crea un link per il commercialista' }))
    const dialog = await screen.findByRole('dialog')
    const expiry = within(dialog).getByRole('combobox', { name: 'Scade tra' })
    expect(expiry).toHaveTextContent('7 giorni')
    await user.click(expiry)
    expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual([
      '1 giorno',
      '3 giorni',
      '7 giorni',
      '14 giorni',
      '30 giorni',
    ])
    await user.click(screen.getByRole('option', { name: '14 giorni' }))
    await user.click(within(dialog).getByRole('button', { name: 'Crea il link' }))

    expect(vi.mocked(api.POST).mock.calls[0]).toEqual([
      '/api/accountant-export/exports/{export_id}/share-links',
      { params: { path: { export_id: 'e-1' } }, body: { days: 14, recipient_email: null } },
    ])
    expect(await within(dialog).findByDisplayValue(CREATED.url)).toBeInTheDocument()
    expect(within(dialog).getByText('Lo vedi solo ora: non lo conserviamo. Se lo perdi, revocalo e creane un altro.')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Copia il link' }))
    expect(writeText).toHaveBeenCalledWith(CREATED.url)
    expect(toast.success).toHaveBeenCalledWith('Link copiato')

    // Closed and opened again, the address is gone: it is held nowhere.
    await user.click(within(dialog).getAllByRole('button', { name: 'Chiudi' }).at(-1) as HTMLElement)
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    await user.click(within(panel).getByRole('button', { name: 'Crea un link per il commercialista' }))
    const again = await screen.findByRole('dialog')
    expect(within(again).queryByDisplayValue(CREATED.url)).toBeNull()
    expect(within(again).getByRole('button', { name: 'Crea il link' })).toBeInTheDocument()
  })

  it('shows the server’s refusal in the dialog and keeps the form', async () => {
    vi.mocked(api.POST).mockReturnValue(refused(422, { code: 'validation_failed', detail: 'Un link dura da 1 a 30 giorni.' }) as never)
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const panel = await openPanel()
    await user.click(await within(panel).findByRole('button', { name: 'Crea un link per il commercialista' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Crea il link' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Un link dura da 1 a 30 giorni.')
    expect(within(dialog).getByRole('button', { name: 'Crea il link' })).toBeEnabled()
  })

  it('revokes on one click, with a toast and no confirmation', async () => {
    linksList = [link({ id: 'l-a' })]
    vi.mocked(api.POST).mockReturnValue(ok(link({ id: 'l-a', state: 'revoked' })) as never)
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const panel = await openPanel()
    await user.click(await within(panel).findByRole('button', { name: 'Revoca' }))
    expect(vi.mocked(api.POST).mock.calls[0]).toEqual([
      '/api/accountant-export/share-links/{link_id}/revoke',
      { params: { path: { link_id: 'l-a' } } },
    ])
    expect(toast.success).toHaveBeenCalledWith('Link revocato: chi lo ha non scarica più')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.queryByRole('alertdialog')).toBeNull()
  })

  it('goes to the openings of a link in «Link condivisi»', async () => {
    linksList = [link({ id: 'l-a', download_count: 2 })]
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    exportsList = [stored()]
    const { onOpenLink } = page({ tab: 'exports', item: 'e-1' })
    await user.click(await screen.findByRole('button', { name: '2 aperture' }))
    expect(onOpenLink).toHaveBeenCalledWith('l-a')
  })

  it('offers no link on an export past its 13 months', async () => {
    exportsList = [stored({ deleted_at: '2026-11-10T03:30:00Z', parts: [] })]
    page({ tab: 'exports', item: 'e-1' })
    const panel = await screen.findByRole('complementary', { name: 'Esportazione aperta' })
    await within(panel).findByText(/Eliminata il/)
    expect(within(panel).queryByRole('button', { name: /Crea un link|Revoca/ })).toBeNull()
  })

  it('is an admin’s: a collaborator on a stale address gets the server’s refusal and no button', async () => {
    session.role = 'collaborator'
    vi.mocked(api.GET).mockImplementation((() =>
      refused(403, { code: 'permission_denied', detail: 'Serve il ruolo admin.' })) as never)
    page({ tab: 'links' })
    expect(await screen.findByText(/Serve il ruolo admin\.|non puoi|admin/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Crea un link|Revoca|Scrivi su Drive/ })).toBeNull()
  })
})

describe('handing an export over: Drive', () => {
  async function openPanel() {
    exportsList = [stored()]
    page({ tab: 'exports', item: 'e-1' })
    return screen.findByRole('complementary', { name: 'Esportazione aperta' })
  }

  it('says the space keeps its files on the server, and draws no button', async () => {
    const panel = await openPanel()
    expect(
      await within(panel).findByText('Questo spazio salva i file sul server, non su Google Drive: la consegna su Drive non è disponibile.'),
    ).toBeInTheDocument()
    expect(within(panel).queryByRole('button', { name: /Drive/ })).toBeNull()
  })

  it('says Drive is not connected yet and points at its page', async () => {
    driveStatus = { available: false, reason: 'not_connected' }
    const panel = await openPanel()
    expect(await within(panel).findByText(/non è ancora collegato/)).toBeInTheDocument()
    expect(within(panel).getByRole('link', { name: 'Apri Google Drive' })).toHaveAttribute('href', '/app/settings/drive')
    expect(within(panel).queryByRole('button', { name: 'Scrivi su Drive' })).toBeNull()
  })

  it('writes to Drive on the press of a quiet button and says where the copy went', async () => {
    driveStatus = { available: true, reason: null }
    const written: DriveWrite = {
      id: 'w-1',
      export_id: 'e-1',
      prefix: 'commercialista/lumen-studio/2026-t3/20261110t104012z',
      files: ['commercialista-lumen.zip'],
      written_at: '2026-11-10T09:40:12Z',
      written_by: 'u-1',
    }
    vi.mocked(api.POST).mockImplementation((() => {
      driveWrites = [written]
      return ok(written)
    }) as never)
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const panel = await openPanel()
    expect(within(panel).queryByText(/Scritta il/)).toBeNull()
    await user.click(await within(panel).findByRole('button', { name: 'Scrivi su Drive' }))
    expect(vi.mocked(api.POST).mock.calls[0]).toEqual([
      '/api/accountant-export/exports/{export_id}/drive-writes',
      { params: { path: { export_id: 'e-1' } } },
    ])
    expect(await within(panel).findByText(/Scritta il .* in commercialista\/lumen-studio\/2026-t3\/20261110t104012z\//)).toBeInTheDocument()
  })

  it('shows a Drive that cannot be reached as a red band with the way to Drive’s page', async () => {
    driveStatus = { available: true, reason: null }
    vi.mocked(api.POST).mockReturnValue(
      refused(409, { code: 'conflict', detail: 'Google Drive non è pronto a ricevere i documenti.' }) as never,
    )
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const panel = await openPanel()
    await user.click(await within(panel).findByRole('button', { name: 'Scrivi su Drive' }))
    const band = await within(panel).findByRole('alert')
    expect(band).toHaveTextContent('Google Drive non è pronto a ricevere i documenti.')
    expect(within(band).getByRole('link', { name: 'Apri Google Drive' })).toBeInTheDocument()
  })
})

describe('«Link condivisi»', () => {
  const ended = link({ id: 'l-b', state: 'expired', expires_at: '2026-10-01T08:00:00Z', download_count: 2 })

  it('lists every link with its export, expiry, openings and state, ended ones muted', async () => {
    linksList = [link({ id: 'l-a', download_count: 5 }), ended]
    page({ tab: 'links' })
    const rows = await screen.findAllByRole('row')
    expect(rows).toHaveLength(3)
    expect(within(rows[1] as HTMLElement).getByText('Lumen Studio S.r.l.')).toBeInTheDocument()
    expect(within(rows[1] as HTMLElement).getByText(/3° trimestre 2026 · creato il 13\/10\/2026/)).toBeInTheDocument()
    expect(within(rows[1] as HTMLElement).getByText('20/10/2026')).toBeInTheDocument()
    expect(within(rows[1] as HTMLElement).getByText('5')).toHaveClass('text-right')
    expect(within(rows[1] as HTMLElement).getByText('Attivo')).toBeInTheDocument()
    expect(rows[2]).toHaveClass('text-muted-foreground')
    expect(within(rows[2] as HTMLElement).getByText('Scaduto').closest('[data-tone]')).toHaveAttribute('data-tone', 'muted')
    expect(screen.queryByRole('button', { name: /Copia/ })).toBeNull()
  })

  it('says there is no link and sends the admin to the exports', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const { onTabChange } = page({ tab: 'links' })
    expect(await screen.findByText('Nessun link')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Vai alle esportazioni' }))
    expect(onTabChange).toHaveBeenCalledWith('exports')
  })

  it('opens a link in the panel: its openings with when and from which address, and «Revoca»', async () => {
    linksList = [
      link({
        id: 'l-a',
        download_count: 2,
        openings: [
          { opened_at: '2026-10-15T10:00:00Z', address: '203.0.113.7', part: 1 },
          { opened_at: '2026-10-14T09:00:00Z', address: '2001:db8::1', part: 1 },
        ],
      }),
    ]
    vi.mocked(api.POST).mockReturnValue(ok(link({ id: 'l-a', state: 'revoked' })) as never)
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    page({ tab: 'links', openLink: 'l-a' })
    const panel = await screen.findByRole('complementary', { name: 'Link aperto' })
    const openings = await within(panel).findByRole('region', { name: 'Aperture' })
    expect(within(openings).getAllByRole('listitem')).toHaveLength(2)
    expect(within(openings).getByText(/203\.0\.113\.7 · ZIP 1/)).toBeInTheDocument()
    expect(within(openings).getByText(/2001:db8::1/)).toBeInTheDocument()
    await user.click(within(panel).getByRole('button', { name: 'Revoca' }))
    expect(vi.mocked(api.POST).mock.calls[0]?.[0]).toBe('/api/accountant-export/share-links/{link_id}/revoke')
    expect(toast.success).toHaveBeenCalled()
  })

  it('shows an ended link with no «Revoca» and a way back to its export', async () => {
    linksList = [{ ...ended, revoked_at: null }]
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const { onOpenExport } = page({ tab: 'links', openLink: 'l-b' })
    const panel = await screen.findByRole('complementary', { name: 'Link aperto' })
    expect(await within(panel).findByText(/Scaduto il 01\/10\/2026/)).toBeInTheDocument()
    expect(within(panel).getByText('Nessuna apertura.')).toBeInTheDocument()
    expect(within(panel).queryByRole('button', { name: 'Revoca' })).toBeNull()
    await user.click(within(panel).getByRole('button', { name: 'Apri l’esportazione' }))
    expect(onOpenExport).toHaveBeenCalledWith('e-1')
  })

  it('opens a link from its row', async () => {
    linksList = [link({ id: 'l-a' })]
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const { onLinkChange } = page({ tab: 'links' })
    await user.click((await screen.findAllByRole('row'))[1] as HTMLElement)
    expect(onLinkChange).toHaveBeenCalledWith('l-a')
  })
})
