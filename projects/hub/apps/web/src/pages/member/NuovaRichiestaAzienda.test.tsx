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
import type { UserEvent } from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CompanyRequest } from '@/lib/api'
import { newCompanyFields, NuovaRichiestaAzienda } from './NuovaRichiestaAzienda'

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
  telefono: '+39 345 1234567',
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
  azienda_remoto: 'remoto',
  azienda_giorni_presenza: null,
  azienda_numero_risorse: 2,
  azienda_figura_richiesta: 'Backend developer',
}

function mount() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const nuovaRichiestaAzienda = createRoute({
    getParentRoute: () => root,
    path: '/me/new-company',
    component: NuovaRichiestaAzienda,
  })
  const me = createRoute({ getParentRoute: () => root, path: '/me', component: () => <h1>La tua area</h1> })
  const router = createRouter({
    routeTree: root.addChildren([nuovaRichiestaAzienda, me]),
    history: createMemoryHistory({ initialEntries: ['/me/new-company'] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('newCompanyFields', () => {
  const base: CompanyRequest = {
    nome_azienda: '',
    figura_richiesta: '',
    referente_nome: '',
    referente_cognome: '',
    email: '',
    telefono: '',
    progetto: '',
    periodo_da: '',
    durata: '',
    budget_giornaliero: '',
    remoto: '',
    giorni_presenza: '',
    numero_risorse: '',
  }

  it('reaches every project answer but the company’s own identity', () => {
    const fields = newCompanyFields()
    expect(fields.map((field) => field.id)).toEqual([
      'figura_richiesta',
      'progetto',
      'periodo_da',
      'budget_giornaliero',
      'remoto',
      'numero_risorse',
    ])
    expect(fields.some((field) => field.id === 'nome_azienda')).toBe(false)
    expect(fields.some((field) => field.id === 'referente')).toBe(false)
  })

  it('keeps the wizard’s own rules, `durata` included', () => {
    const periodo = newCompanyFields().find((field) => field.id === 'periodo_da')!
    expect(periodo.validate({ ...base, periodo_da: '2027-01-15', durata: '' })).not.toBeNull()
    expect(periodo.validate({ ...base, periodo_da: '2027-01-15', durata: '6 mesi' })).toBeNull()
  })
})

describe('/me/new-company', () => {
  /** Fills every required field with a value that passes client-side validation, so a
   *  test can then focus on the one thing it is about (a short `progetto`, a server
   *  refusal) without every other field's own validation blocking the submit first. */
  async function fillValid(user: UserEvent) {
    await user.type(await screen.findByLabelText('Figura richiesta'), 'Data engineer')
    await user.type(
      screen.getByLabelText('Progetto'),
      'Serve un data engineer per un progetto di sei mesi.',
    )
    await user.type(screen.getByLabelText('Da quando'), '2027-01-15')
    await user.type(screen.getByLabelText('Per quanto'), '6 mesi')
    await user.type(screen.getByLabelText('Budget a giornata'), '650')
    await user.click(screen.getByRole('radio', { name: /Da remoto/ }))
    await user.type(screen.getByLabelText('Numero di persone'), '2')
  }

  it('starts blank (never pre-filled from the newest request) and posts every answer', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (_url, init) =>
        init?.method === 'POST' && (_url as string) === '/api/hub/me/company'
          ? answer(201, { ...PROFILE, durata: '6 mesi', figura_richiesta: 'Data engineer' })
          : answer(200, PROFILE),
      )
    mount()
    const user = userEvent.setup()

    // Never pre-filled from `GET /me`'s own newest request: this is a fresh request,
    // not an edit, so the fields start empty.
    expect(await screen.findByLabelText('Figura richiesta')).toHaveValue('')
    await fillValid(user)

    await user.click(screen.getByRole('button', { name: 'Invia la richiesta' }))
    await screen.findByRole('heading', { name: 'La tua area' })

    const post = fetchSpy.mock.calls.find(
      ([url, init]) => url === '/api/hub/me/company' && init?.method === 'POST',
    )!
    expect(JSON.parse(post[1]!.body as string)).toEqual({
      progetto: 'Serve un data engineer per un progetto di sei mesi.',
      periodo_da: '2027-01-15',
      durata: '6 mesi',
      budget_giornaliero: '650',
      remoto: 'remoto',
      giorni_presenza: null,
      numero_risorse: 2,
      figura_richiesta: 'Data engineer',
    })
    // Never a PATCH: a new row, not an edit of the request already on file.
    expect(fetchSpy.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false)
  })

  it('refuses a project description that is too short before it posts', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => answer(200, PROFILE))
    mount()
    const user = userEvent.setup()
    await fillValid(user)
    // Overwrite the one field this test is about, after every other field already
    // passes, so only its own error shows.
    const progetto = screen.getByLabelText('Progetto')
    await user.clear(progetto)
    await user.type(progetto, 'troppo corto')
    await user.click(screen.getByRole('button', { name: 'Invia la richiesta' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Due righe bastano')
    expect(fetchSpy.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it('shows a server refusal under the field it names', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) =>
      init?.method === 'POST' && url === '/api/hub/me/company'
        ? answer(422, {
            detail: [{ loc: ['body', 'budget_giornaliero'], msg: 'serve una cifra più bassa' }],
          })
        : answer(200, PROFILE),
    )
    mount()
    const user = userEvent.setup()
    await fillValid(user)
    await user.click(screen.getByRole('button', { name: 'Invia la richiesta' }))
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('serve una cifra più bassa'),
    )
  })

  it('cancels back to the member area without posting', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => answer(200, PROFILE))
    mount()
    await screen.findByLabelText('Progetto')
    await userEvent.setup().click(screen.getByRole('link', { name: 'Annulla' }))
    await screen.findByRole('heading', { name: 'La tua area' })
    expect(fetchSpy.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it('redirects to the member area on a direct visit with no company yet (Greptile, PR #313)', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(answer(200, { ...PROFILE, ha_azienda: false }))
    mount()
    await screen.findByRole('heading', { name: 'La tua area' })
    expect(screen.queryByLabelText('Progetto')).toBeNull()
    expect(fetchSpy.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })
})
