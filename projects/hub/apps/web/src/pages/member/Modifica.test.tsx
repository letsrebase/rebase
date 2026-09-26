import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { editFields, Modifica } from './Modifica'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const PROFILE = {
  id: 'f1',
  nome: 'Ada',
  cognome: 'Lovelace',
  email: 'ada@studio.it',
  linkedin_url: null,
  ha_scheda: true,
  cv_filename: 'Ada CV.pdf',
  cv_size: 2048,
  tariffa_giornaliera: '450.00',
  posizione: 'Backend developer',
  remoto: 'remoto',
  links: [],
  created_at: '2026-09-10T10:00:00Z',
  updated_at: '2026-09-10T10:00:00Z',
  completa: true,
}

/** The same card without a CV: what an admin's research leaves for the person to add
 *  (ORB-155), with everything else already answered so the CV is the one thing missing. */
const WITHOUT_CV = { ...PROFILE, cv_filename: null, cv_size: null, completa: false }

function mount() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const edit = createRoute({ getParentRoute: () => root, path: '/me/edit', component: Modifica })
  const me = createRoute({ getParentRoute: () => root, path: '/me', component: () => <h1>La tua area</h1> })
  const router = createRouter({
    routeTree: root.addChildren([edit, me]),
    history: createMemoryHistory({ initialEntries: ['/me/edit'] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('the edit fields', () => {
  const base = { ...PROFILE, linkedin_url: '', cv: null }

  it('leave the email out and make the CV optional when we already hold one', () => {
    const fields = editFields(true)
    expect(fields.map((field) => field.id)).not.toContain('email')
    const cv = fields.find((field) => field.id === 'cv')!
    expect(cv.optional).toBe(true)
    expect(cv.validate(base as never)).toBeNull()
    const png = new File(['x'], 'cv.png', { type: 'image/png' })
    expect(cv.validate({ ...base, cv: png } as never)).not.toBeNull()
  })

  it('ask for the CV without demanding it when there is none to keep, and say so', () => {
    const fields = editFields(false)
    expect(fields.map((field) => field.id)).not.toContain('email')
    const cv = fields.find((field) => field.id === 'cv')!
    // Optional here too since the wizard stopped demanding a PDF: requiring it on this
    // page would be the same wall one step later, in front of somebody who came to
    // change their rate.
    expect(cv.optional).toBe(true)
    expect(cv.validate(base as never)).toBeNull()
    expect(cv.hint).toContain('Non ne abbiamo ancora uno')
    const pdf = new File(['%PDF'], 'cv.pdf', { type: 'application/pdf' })
    expect(cv.validate({ ...base, cv: pdf } as never)).toBeNull()
  })

  it('keep the wizard’s rules for everything else', () => {
    const linkedin = editFields(true).find((field) => field.id === 'linkedin_url')!
    expect(linkedin.validate({ ...base, linkedin_url: 'https://twitter.com/ada' } as never)).not.toBeNull()
  })
})

describe('/me/edit', () => {
  it('starts from the current answers and saves them with PATCH', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (_url, init) =>
        init?.method === 'PATCH'
          ? answer(200, { ...PROFILE, posizione: 'Staff engineer' })
          : answer(200, PROFILE),
      )
    mount()
    const user = userEvent.setup()
    const posizione = await screen.findByLabelText('Posizione')
    expect(posizione).toHaveValue('Backend developer')
    await user.clear(posizione)
    await user.type(posizione, 'Staff engineer')
    await user.click(screen.getByRole('button', { name: 'Salva' }))
    await screen.findByRole('heading', { name: 'La tua area' })
    const patch = fetchSpy.mock.calls.find(([, init]) => init?.method === 'PATCH')!
    expect(patch[0]).toBe('/api/hub/me')
    expect(JSON.parse(patch[1]!.body as string)).toEqual({
      nome: 'Ada',
      cognome: 'Lovelace',
      linkedin_url: null,
      tariffa_giornaliera: '450.00',
      posizione: 'Staff engineer',
      remoto: 'remoto',
      links: [],
    })
    expect(fetchSpy.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false)
  })

  it.each([
    ['1.500', '1500.00'],
    ['1.234,50', '1234.50'],
  ])('saves a rate typed as «%s» as %s (REB-485)', async (typed, sent) => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => answer(200, PROFILE))
    mount()
    const user = userEvent.setup()
    const rate = await screen.findByLabelText('Tariffa a giornata')
    expect(rate).toHaveValue('450.00')
    await user.clear(rate)
    await user.type(rate, typed)
    await user.click(screen.getByRole('button', { name: 'Salva' }))
    await screen.findByRole('heading', { name: 'La tua area' })
    const patch = fetchSpy.mock.calls.find(([, init]) => init?.method === 'PATCH')!
    expect(JSON.parse(patch[1]!.body as string).tariffa_giornaliera).toBe(sent)
  })

  it('refuses a rate whose thousands put it over the ceiling, before it saves (REB-485)', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => answer(200, PROFILE))
    mount()
    const user = userEvent.setup()
    const rate = await screen.findByLabelText('Tariffa a giornata')
    await user.clear(rate)
    // 150000: read as 150 it would have been saved.
    await user.type(rate, '150.000')
    await user.click(screen.getByRole('button', { name: 'Salva' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Serve una cifra, in euro.')
    expect(fetchSpy.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false)
  })

  it('shows a stored profile as its name and saves it back as the profile (ORB-203)', async () => {
    const WITH_LINKEDIN = { ...PROFILE, linkedin_url: 'https://www.linkedin.com/in/ada' }
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async () => answer(200, WITH_LINKEDIN))
    mount()
    const user = userEvent.setup()
    const linkedin = await screen.findByLabelText('Profilo LinkedIn')
    expect(linkedin).toHaveValue('ada')
    await user.click(screen.getByRole('button', { name: 'Salva' }))
    await screen.findByRole('heading', { name: 'La tua area' })
    const patch = fetchSpy.mock.calls.find(([, init]) => init?.method === 'PATCH')!
    expect(JSON.parse(patch[1]!.body as string).linkedin_url).toBe('https://www.linkedin.com/in/ada')
  })

  it('saves a card that has no CV, and says the CV can still arrive', async () => {
    // A fresh Response per call, never `mockResolvedValue(answer(...))`: a body is read
    // once, so the second request of this test (the PATCH) would find it consumed and
    // the save would fail for a reason that has nothing to do with the CV.
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async () => answer(200, WITHOUT_CV))
    mount()
    const user = userEvent.setup()
    await screen.findByLabelText('Posizione')
    // The heading now carries «(facoltativo)», so the region's accessible name does too.
    const cvSection = screen.getByRole('region', { name: /Il tuo CV/ })
    expect(within(cvSection).getByText('(facoltativo)')).toBeInTheDocument()
    expect(cvSection).toHaveTextContent('Non ne abbiamo ancora uno')

    await user.click(screen.getByRole('button', { name: 'Salva' }))

    await screen.findByRole('heading', { name: 'La tua area' })
    expect(screen.queryByRole('alert')).toBeNull()
    const patch = fetchSpy.mock.calls.find(([, init]) => init?.method === 'PATCH')!
    expect(patch[0]).toBe('/api/hub/me')
    // Nothing was uploaded: the PUT is the CV route and nobody chose a file.
    expect(fetchSpy.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false)
  })

  it('shows a server refusal under the field it names', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) =>
      init?.method === 'PATCH'
        ? answer(422, {
            detail: [{ loc: ['body', 'tariffa_giornaliera'], msg: 'serve una cifra più bassa' }],
          })
        : answer(200, PROFILE),
    )
    mount()
    const user = userEvent.setup()
    await screen.findByLabelText('Posizione')
    await user.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('serve una cifra più bassa'),
    )
  })

  it('says the rest was saved when only the CV is refused', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) => {
      if (init?.method === 'PATCH') return answer(200, PROFILE)
      if (init?.method === 'PUT') {
        return answer(422, { detail: [{ loc: ['body', 'cv'], msg: 'il CV deve essere un PDF' }] })
      }
      return answer(200, PROFILE)
    })
    mount()
    const user = userEvent.setup()
    await screen.findByLabelText('Posizione')
    await user.upload(
      screen.getByLabelText('CV'),
      new File(['%PDF'], 'cv.pdf', { type: 'application/pdf' }),
    )
    await user.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => {
      const alert = screen.getByRole('alert')
      expect(alert).toHaveTextContent('il CV deve essere un PDF')
      expect(alert).toHaveTextContent('Le altre risposte sono salvate')
    })
    expect(screen.getByRole('button', { name: 'Salva' })).toBeInTheDocument()
  })

  it('redirects to the member area on a direct visit with no freelancer card yet', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(answer(200, { ...PROFILE, ha_scheda: false }))
    mount()
    await screen.findByRole('heading', { name: 'La tua area' })
    expect(screen.queryByLabelText('Posizione')).toBeNull()
    expect(fetchSpy.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false)
  })
})
