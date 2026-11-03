import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  useNavigate,
} from '@tanstack/react-router'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CompanyRequest } from '@/lib/api'
import { ME_KEY } from '@/lib/me'
import { editCompanyFields, EditCompany } from './EditCompany'

// Wraps the real `useNavigate` for every test but one: the redirect-race test below
// swaps in a no-op spy so it can prove the render-time guard withholds the form on its
// own, independent of whether the navigation away has actually landed yet.
vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return { ...actual, useNavigate: vi.fn(actual.useNavigate) }
})

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** The request filed first, and the one filed afterwards (REB-602): `requests` is
 *  newest first, so the id-less page starts from `NEWEST` and `/r1` from `OLDER`. */
const OLDER = {
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
}
const NEWEST = {
  id: 'r2',
  requested_role: 'Data engineer',
  project: 'Serve un data engineer per costruire la pipeline dei dati.',
  period_from: '2026-11-02',
  duration: '2 mesi',
  daily_budget: '550.00',
  remote: 'remote',
  onsite_days: null,
  headcount: 1,
  created_at: '2026-09-29T09:00:00Z',
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
  requests: [NEWEST, OLDER],
}

function mount(path = '/me/edit-company') {
  const root = createRootRoute({ component: () => <Outlet /> })
  const editLegalEntity = createRoute({
    getParentRoute: () => root,
    path: '/me/edit-company',
    component: EditCompany,
  })
  const editRequest = createRoute({
    getParentRoute: () => root,
    path: '/me/edit-company/$id',
    component: EditCompany,
  })
  const me = createRoute({ getParentRoute: () => root, path: '/me', component: () => <h1>La tua area</h1> })
  const router = createRouter({
    routeTree: root.addChildren([editLegalEntity, editRequest, me]),
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { client, router }
}

afterEach(() => vi.restoreAllMocks())

describe('the edit fields', () => {
  const base: CompanyRequest = {
    company_name: '',
    requested_role: 'Backend developer',
    contact_first_name: '',
    contact_last_name: '',
    email: '',
    phone: '',
    project: 'Serve un backend developer per tre mesi, da ottobre.',
    period_from: '2026-10-01',
    duration: '3 mesi',
    daily_budget: '500',
    remote: 'remote',
    onsite_days: '',
    headcount: '2',
  }

  it('reaches the seven project answers, never the company’s own identity (REB-380)', () => {
    const fields = editCompanyFields()
    expect(fields.map((field) => field.id)).toEqual([
      'requested_role',
      'project',
      'period_from',
      'daily_budget',
      'remote',
      'headcount',
    ])
  })

  it('keeps the wizard’s own rules', () => {
    const project = editCompanyFields().find((field) => field.id === 'project')!
    expect(project.validate({ ...base, project: 'troppo corto' })).not.toBeNull()
    expect(project.validate(base)).toBeNull()
  })
})

describe('/me/edit-company', () => {
  it('starts from the newest request and saves every answer with PATCH by its id', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (_url, init) =>
        init?.method === 'PATCH'
          ? answer(200, { ...PROFILE, requests: [{ ...NEWEST, duration: '4 mesi' }, OLDER] })
          : answer(200, PROFILE),
      )
    mount()
    const user = userEvent.setup()
    const duration = await screen.findByLabelText('Per quanto')
    expect(duration).toHaveValue('2 mesi')
    expect(screen.getByText(/^Progetto, periodo e budget della richiesta «Data engineer»\.$/)).toBeInTheDocument()
    await user.clear(duration)
    await user.type(duration, '4 mesi')
    const budget = screen.getByLabelText('Budget a giornata')
    await user.clear(budget)
    await user.type(budget, '600')
    await user.click(screen.getByRole('button', { name: 'Salva' }))
    await screen.findByRole('heading', { name: 'La tua area' })
    const patch = fetchSpy.mock.calls.find(([, init]) => init?.method === 'PATCH')!
    expect(patch[0]).toBe('/api/hub/me/company/r2')
    expect(JSON.parse(patch[1]!.body as string)).toEqual({
      project: NEWEST.project,
      period_from: NEWEST.period_from,
      duration: '4 mesi',
      daily_budget: '600.00',
      remote: NEWEST.remote,
      onsite_days: NEWEST.onsite_days,
      headcount: NEWEST.headcount,
      requested_role: NEWEST.requested_role,
    })
  })

  it('keeps saving the request it opened when a newer one is filed in another tab (REB-602)', async () => {
    const filedElsewhere = {
      ...NEWEST,
      id: 'r3',
      requested_role: 'Product designer',
      duration: '9 mesi',
      created_at: '2026-09-30T09:00:00Z',
    }
    let profile: unknown = PROFILE
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (_url, init) =>
        init?.method === 'PATCH' ? answer(200, PROFILE) : answer(200, profile),
      )
    const { client } = mount()
    const user = userEvent.setup()
    expect(await screen.findByLabelText('Per quanto')).toHaveValue('2 mesi')

    // Back on this tab, `GET /me` answers with a new newest request.
    profile = { ...PROFILE, requests: [filedElsewhere, NEWEST, OLDER] }
    await client.invalidateQueries({ queryKey: ME_KEY })
    await waitFor(() =>
      expect(fetchSpy.mock.calls.filter(([url]) => url === '/api/hub/me')).toHaveLength(2),
    )

    await user.click(screen.getByRole('button', { name: 'Salva' }))
    await screen.findByRole('heading', { name: 'La tua area' })
    const patch = fetchSpy.mock.calls.find(([, init]) => init?.method === 'PATCH')!
    expect(patch[0]).toBe('/api/hub/me/company/r2')
    expect(JSON.parse(patch[1]!.body as string).duration).toBe('2 mesi')
  })

  it('starts the form over when the address jumps from one request to another (REB-602)', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => answer(200, PROFILE))
    const { router } = mount('/me/edit-company/r1')
    const user = userEvent.setup()
    expect(await screen.findByLabelText('Per quanto')).toHaveValue('3 mesi')

    await act(() => router.navigate({ to: '/me/edit-company/$id', params: { id: 'r2' } }))
    await waitFor(() => expect(screen.getByLabelText('Per quanto')).toHaveValue('2 mesi'))
    await user.click(screen.getByRole('button', { name: 'Salva' }))
    await screen.findByRole('heading', { name: 'La tua area' })
    const patch = fetchSpy.mock.calls.find(([, init]) => init?.method === 'PATCH')!
    expect(patch[0]).toBe('/api/hub/me/company/r2')
    expect(JSON.parse(patch[1]!.body as string).project).toBe(NEWEST.project)
  })

  it('opens an older request by its id and saves that one, not the newest (REB-602)', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (_url, init) =>
        init?.method === 'PATCH'
          ? answer(200, { ...PROFILE, requests: [NEWEST, { ...OLDER, duration: '5 mesi' }] })
          : answer(200, PROFILE),
      )
    mount('/me/edit-company/r1')
    const user = userEvent.setup()
    const duration = await screen.findByLabelText('Per quanto')
    expect(duration).toHaveValue('3 mesi')
    expect(screen.getByLabelText('Budget a giornata')).toHaveValue('500.00')
    expect(screen.getByText(/^Progetto, periodo e budget della richiesta «Backend developer»\.$/)).toBeInTheDocument()
    await user.clear(duration)
    await user.type(duration, '5 mesi')
    await user.click(screen.getByRole('button', { name: 'Salva' }))
    await screen.findByRole('heading', { name: 'La tua area' })
    const patch = fetchSpy.mock.calls.find(([, init]) => init?.method === 'PATCH')!
    expect(patch[0]).toBe('/api/hub/me/company/r1')
    expect(JSON.parse(patch[1]!.body as string).duration).toBe('5 mesi')
  })

  it('saves a budget typed as «1.500» as 1500 (REB-485)', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => answer(200, PROFILE))
    mount('/me/edit-company/r1')
    const user = userEvent.setup()
    const budget = await screen.findByLabelText('Budget a giornata')
    expect(budget).toHaveValue('500.00')
    await user.clear(budget)
    await user.type(budget, '1.500')
    await user.click(screen.getByRole('button', { name: 'Salva' }))
    await screen.findByRole('heading', { name: 'La tua area' })
    const patch = fetchSpy.mock.calls.find(([, init]) => init?.method === 'PATCH')!
    expect(JSON.parse(patch[1]!.body as string).daily_budget).toBe('1500.00')
  })

  it('refuses a project description that is too short before it posts', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => answer(200, PROFILE))
    mount()
    const user = userEvent.setup()
    const project = await screen.findByLabelText('Progetto')
    await user.clear(project)
    await user.type(project, 'troppo corto')
    await user.click(screen.getByRole('button', { name: 'Salva' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Due righe bastano')
    expect(fetchSpy.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false)
  })

  it('shows a server refusal under the field it names', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) =>
      init?.method === 'PATCH'
        ? answer(422, {
            detail: [{ loc: ['body', 'daily_budget'], msg: 'serve una cifra più bassa' }],
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

  it('redirects to the member area on a direct visit with no company yet (REB-383)', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(answer(200, { ...PROFILE, has_company: false, requests: [] }))
    mount()
    await screen.findByRole('heading', { name: 'La tua area' })
    expect(screen.queryByLabelText('Progetto')).toBeNull()
    expect(fetchSpy.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false)
  })

  it('redirects for an id the person does not have, and never builds a form for it (REB-602)', async () => {
    // The server would answer 404 to a request of somebody else's or one an admin
    // deleted; the page says so before the person types a word.
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, PROFILE))
    mount('/me/edit-company/not-mine')
    await screen.findByRole('heading', { name: 'La tua area' })
    expect(screen.queryByLabelText('Progetto')).toBeNull()
    expect(fetchSpy.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false)
  })

  it('withholds the form on its own even while the redirect is stalled (Greptile, PR #315)', async () => {
    // A no-op `navigate`: real navigation never lands here, so `queryByLabelText`
    // below is checked while the page would still be showing a form if only the
    // `useEffect` redirect (and not the render-time `if (missing) return null`)
    // were the thing keeping it away.
    const navigateSpy = vi.fn()
    vi.mocked(useNavigate).mockReturnValue(navigateSpy)
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { ...PROFILE, has_company: false, requests: [] }),
    )
    mount()
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith({ to: '/me', replace: true }))
    expect(screen.queryByLabelText('Progetto')).toBeNull()
  })
})
