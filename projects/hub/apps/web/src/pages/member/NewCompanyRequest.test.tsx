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
import { newCompanyFields, NewCompanyRequest } from './NewCompanyRequest'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const PROFILE = {
  id: 'c1',
  first_name: 'Wile',
  last_name: 'E.',
  email: 'wile@acme.it',
  linkedin_url: null,
  phone: '+39 345 1234567',
  role: 'member',
  created_at: '2026-09-10T10:00:00Z',
  updated_at: '2026-09-10T10:00:00Z',
  has_card: false,
  cv_filename: null,
  cv_size: null,
  daily_rate: null,
  job_title: null,
  remote: null,
  links: [],
  complete: false,
  has_company: true,
  requests: [
    {
      id: 'r1',
      requested_role: 'Backend developer',
      project: 'Serve un backend developer per tre mesi, da ottobre.',
      period_from: '2026-10-01',
      duration: '3 mesi',
      daily_budget: '500.00',
      remote: 'remote',
      onsite_days: null,
      headcount: 2,
      created_at: '2026-09-20T10:00:00Z',
    },
  ],
}

function mount() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const newRequestLegalEntity = createRoute({
    getParentRoute: () => root,
    path: '/me/new-company',
    component: NewCompanyRequest,
  })
  const me = createRoute({ getParentRoute: () => root, path: '/me', component: () => <h1>La tua area</h1> })
  const router = createRouter({
    routeTree: root.addChildren([newRequestLegalEntity, me]),
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
    company_name: '',
    requested_role: '',
    contact_first_name: '',
    contact_last_name: '',
    email: '',
    phone: '',
    project: '',
    period_from: '',
    duration: '',
    daily_budget: '',
    remote: '',
    onsite_days: '',
    headcount: '',
  }

  it('reaches every project answer but the company’s own identity', () => {
    const fields = newCompanyFields()
    expect(fields.map((field) => field.id)).toEqual([
      'requested_role',
      'project',
      'period_from',
      'daily_budget',
      'remote',
      'headcount',
    ])
    expect(fields.some((field) => field.id === 'company_name')).toBe(false)
    expect(fields.some((field) => field.id === 'contact_person')).toBe(false)
  })

  it('keeps the wizard’s own rules, `durata` included', () => {
    const period = newCompanyFields().find((field) => field.id === 'period_from')!
    expect(period.validate({ ...base, period_from: '2027-01-15', duration: '' })).not.toBeNull()
    expect(period.validate({ ...base, period_from: '2027-01-15', duration: '6 mesi' })).toBeNull()
  })
})

describe('/me/new-company', () => {
  /** Fills every required field with a value that passes client-side validation, so a
   *  test can then focus on the one thing it is about (a short `project`, a server
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
          ? answer(201, PROFILE)
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
      project: 'Serve un data engineer per un progetto di sei mesi.',
      period_from: '2027-01-15',
      duration: '6 mesi',
      daily_budget: '650.00',
      remote: 'remote',
      onsite_days: null,
      headcount: 2,
      requested_role: 'Data engineer',
    })
    // Never a PATCH: a new row, not an edit of the request already on file.
    expect(fetchSpy.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false)
  })

  it('posts a budget typed as «1.500» as 1500 (REB-485)', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (url, init) =>
        init?.method === 'POST' && url === '/api/hub/me/company' ? answer(201, PROFILE) : answer(200, PROFILE),
      )
    mount()
    const user = userEvent.setup()
    await fillValid(user)
    const budget = screen.getByLabelText('Budget a giornata')
    await user.clear(budget)
    await user.type(budget, '1.500')
    await user.click(screen.getByRole('button', { name: 'Invia la richiesta' }))
    await screen.findByRole('heading', { name: 'La tua area' })
    const post = fetchSpy.mock.calls.find(
      ([url, init]) => url === '/api/hub/me/company' && init?.method === 'POST',
    )!
    expect(JSON.parse(post[1]!.body as string).daily_budget).toBe('1500.00')
  })

  it('refuses a project description that is too short before it posts', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => answer(200, PROFILE))
    mount()
    const user = userEvent.setup()
    await fillValid(user)
    // Overwrite the one field this test is about, after every other field already
    // passes, so only its own error shows.
    const project = screen.getByLabelText('Progetto')
    await user.clear(project)
    await user.type(project, 'troppo corto')
    await user.click(screen.getByRole('button', { name: 'Invia la richiesta' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Due righe bastano')
    expect(fetchSpy.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it('shows a server refusal under the field it names', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) =>
      init?.method === 'POST' && url === '/api/hub/me/company'
        ? answer(422, {
            detail: [{ loc: ['body', 'daily_budget'], msg: 'serve una cifra più bassa' }],
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
      .mockResolvedValue(answer(200, { ...PROFILE, has_company: false, requests: [] }))
    mount()
    await screen.findByRole('heading', { name: 'La tua area' })
    expect(screen.queryByLabelText('Progetto')).toBeNull()
    expect(fetchSpy.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })
})
