import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CompanyRequest } from '@/lib/api'
import { editCompanyFields, ModificaAzienda } from './ModificaAzienda'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const PROFILE = {
  id: 'c1',
  nome: 'Wile',
  cognome: 'E.',
  email: 'wile@acme.it',
  linkedin_url: null,
  role: 'member',
  created_at: '2026-09-10T10:00:00Z',
  updated_at: '2026-09-10T10:00:00Z',
  ha_scheda: false,
  cv_filename: null,
  cv_size: null,
  tariffa_giornaliera: null,
  posizione: null,
  remoto: null,
  links: [],
  completa: false,
  ha_azienda: true,
  progetto: 'Serve un backend developer per tre mesi, da ottobre.',
  periodo_da: '2026-10-01',
  durata: '3 mesi',
  budget_giornaliero: '500.00',
}

function mount() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const modificaAzienda = createRoute({
    getParentRoute: () => root,
    path: '/me/edit-company',
    component: ModificaAzienda,
  })
  const me = createRoute({ getParentRoute: () => root, path: '/me', component: () => <h1>La tua area</h1> })
  const router = createRouter({
    routeTree: root.addChildren([modificaAzienda, me]),
    history: createMemoryHistory({ initialEntries: ['/me/edit-company'] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('the edit fields', () => {
  const base: CompanyRequest = {
    nome_azienda: '',
    referente_nome: '',
    referente_cognome: '',
    email: '',
    progetto: 'Serve un backend developer per tre mesi, da ottobre.',
    periodo_da: '2026-10-01',
    durata: '3 mesi',
    budget_giornaliero: '500',
  }

  it('reaches only the four project answers, never the company’s own identity', () => {
    const fields = editCompanyFields()
    expect(fields.map((field) => field.id)).toEqual(['progetto', 'periodo_da', 'budget_giornaliero'])
  })

  it('keeps the wizard’s own rules', () => {
    const progetto = editCompanyFields().find((field) => field.id === 'progetto')!
    expect(progetto.validate({ ...base, progetto: 'troppo corto' })).not.toBeNull()
    expect(progetto.validate(base)).toBeNull()
  })
})

describe('/me/edit-company', () => {
  it('starts from the most recent request and saves the four answers with PATCH', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (_url, init) =>
        init?.method === 'PATCH'
          ? answer(200, { ...PROFILE, durata: '4 mesi', budget_giornaliero: '600.00' })
          : answer(200, PROFILE),
      )
    mount()
    const user = userEvent.setup()
    const durata = await screen.findByLabelText('Per quanto')
    expect(durata).toHaveValue('3 mesi')
    await user.clear(durata)
    await user.type(durata, '4 mesi')
    const budget = screen.getByLabelText('Budget a giornata')
    await user.clear(budget)
    await user.type(budget, '600')
    await user.click(screen.getByRole('button', { name: 'Salva' }))
    await screen.findByRole('heading', { name: 'La tua area' })
    const patch = fetchSpy.mock.calls.find(([, init]) => init?.method === 'PATCH')!
    expect(patch[0]).toBe('/api/hub/me/company')
    expect(JSON.parse(patch[1]!.body as string)).toEqual({
      progetto: PROFILE.progetto,
      periodo_da: PROFILE.periodo_da,
      durata: '4 mesi',
      budget_giornaliero: '600',
    })
  })

  it('refuses a project description that is too short before it posts', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => answer(200, PROFILE))
    mount()
    const user = userEvent.setup()
    const progetto = await screen.findByLabelText('Progetto')
    await user.clear(progetto)
    await user.type(progetto, 'troppo corto')
    await user.click(screen.getByRole('button', { name: 'Salva' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Due righe bastano')
    expect(fetchSpy.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false)
  })

  it('shows a server refusal under the field it names', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) =>
      init?.method === 'PATCH'
        ? answer(422, {
            detail: [{ loc: ['body', 'budget_giornaliero'], msg: 'serve una cifra più bassa' }],
          })
        : answer(200, PROFILE),
    )
    mount()
    const user = userEvent.setup()
    await screen.findByLabelText('Progetto')
    await user.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('serve una cifra più bassa'),
    )
  })
})
