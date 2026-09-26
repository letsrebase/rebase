import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FreelancerApplication } from '@/lib/api'
import { loadDraft } from '@/wizard/draft'
import { FREELANCER_DRAFT_KEY, FREELANCER_FIELDS, FreelancerWizard, readPerk } from './FreelancerWizard'

vi.mock('@rebase/analytics/browser', () => ({
  capture: vi.fn(),
  distinctId: vi.fn(() => 'anon-1'),
  identifyUser: vi.fn(),
  resetUser: vi.fn(),
}))
import { capture } from '@rebase/analytics/browser'

/** The wizard mounted on its own little router, so `navigate` has somewhere to go.
 *  `strict` wraps it the way `main.tsx` does, so React 19 simulates the unmount and
 *  remount of every effect. */
function mount(path = '/freelance?utm_source=linkedin&da=pigrocrm', { strict = false } = {}) {
  const root = createRootRoute({ component: () => <Outlet /> })
  const freelance = createRoute({ getParentRoute: () => root, path: '/freelance', component: FreelancerWizard })
  const thanks = createRoute({
    getParentRoute: () => root,
    path: '/thanks',
    validateSearch: (s: Record<string, unknown>) => ({ chi: String(s.chi ?? '') }),
    component: () => <h1>Grazie</h1>,
  })
  const router = createRouter({
    routeTree: root.addChildren([freelance, thanks]),
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  const app = <RouterProvider router={router} />
  render(strict ? <StrictMode>{app}</StrictMode> : app)
  return router
}

// The draft lives in `localStorage`, which jsdom keeps across the tests of one file.
beforeEach(() => window.localStorage.clear())

afterEach(() => {
  vi.restoreAllMocks()
  vi.clearAllMocks()
  window.localStorage.clear()
})

/** The eight answers, up to the review screen, with the day rate as typed. */
async function walkToReview(user: ReturnType<typeof userEvent.setup>, rate = '450') {
  await user.type(await screen.findByLabelText('Nome'), 'Ada')
  await user.type(screen.getByLabelText('Cognome'), 'Lovelace{Enter}')
  await user.type(screen.getByLabelText('Email'), 'ada@studio.it{Enter}')
  await user.keyboard('{Enter}') // LinkedIn, optional
  const cv = new File(['%PDF-1.7'], 'Ada CV.pdf', { type: 'application/pdf' })
  await user.upload(screen.getByLabelText('CV'), cv)
  await user.click(screen.getByRole('button', { name: /Avanti/ }))
  await user.type(screen.getByLabelText('Tariffa a giornata'), `${rate}{Enter}`)
  await user.type(screen.getByLabelText('Posizione'), 'Backend developer{Enter}')
  await user.click(screen.getByRole('radio', { name: /Da remoto/ }))
  await user.click(screen.getByRole('button', { name: /Rivedi|Avanti/ }))
  await user.type(screen.getByLabelText('Link aggiuntivi'), 'https://github.com/ada')
  await user.click(screen.getByRole('button', { name: /Rivedi/ }))
  expect(screen.getByRole('heading', { name: 'Tutto giusto?' })).toBeInTheDocument()
}

/** What was captured under one event name, in order. */
function captured(event: string) {
  return vi
    .mocked(capture)
    .mock.calls.filter(([name]) => name === event)
    .map(([, properties]) => properties)
}

describe('the freelancer fields', () => {
  const base = FREELANCER_FIELDS.reduce(
    (form, field) => ({ ...form, [field.id]: '' }),
    {} as Record<string, unknown>,
  )
  const field = (id: string) => FREELANCER_FIELDS.find((f) => f.id === id)!

  it('accept an empty LinkedIn and refuse one that is not on linkedin.com', () => {
    const validate = field('linkedin_url').validate
    expect(validate({ ...base, linkedin_url: '' } as never)).toBeNull()
    expect(validate({ ...base, linkedin_url: 'https://www.linkedin.com/in/ada' } as never)).toBeNull()
    expect(validate({ ...base, linkedin_url: 'https://twitter.com/ada' } as never)).not.toBeNull()
  })

  it('take the LinkedIn name alone, or the address in whatever shape it was pasted (ORB-203)', () => {
    const { validate, summary } = field('linkedin_url')
    for (const pasted of ['ada', 'linkedin.com/in/ada', 'http://it.linkedin.com/in/ada/?trk=x']) {
      expect(validate({ ...base, linkedin_url: pasted } as never)).toBeNull()
      expect(summary!({ ...base, linkedin_url: pasted } as never)).toBe('https://www.linkedin.com/in/ada')
    }
  })

  it('read the Italian comma in the rate', () => {
    const validate = field('tariffa_giornaliera').validate
    expect(validate({ ...base, tariffa_giornaliera: '450,50' } as never)).toBeNull()
    expect(validate({ ...base, tariffa_giornaliera: 'tanto' } as never)).not.toBeNull()
  })

  it('read the Italian thousands in the rate (REB-485)', () => {
    const validate = field('tariffa_giornaliera').validate
    expect(validate({ ...base, tariffa_giornaliera: '1.500' } as never)).toBeNull()
    // 150000, over the ceiling: read as 150 it would have gone through.
    expect(validate({ ...base, tariffa_giornaliera: '150.000' } as never)).not.toBeNull()
  })

  it('want a PDF under five megabytes, or no CV at all', () => {
    const validate = field('cv').validate
    const pdf = new File(['%PDF'], 'cv.pdf', { type: 'application/pdf' })
    const png = new File(['x'], 'cv.png', { type: 'image/png' })
    expect(validate({ ...base, cv: pdf } as never)).toBeNull()
    expect(validate({ ...base, cv: png } as never)).not.toBeNull()
    // Optional since a PDF nobody had to hand was ending the form here: the step is
    // marked optional, so the wizard shows «(facoltativo)» and lets it through, and
    // the person adds the CV from their area.
    expect(validate({ ...base, cv: null } as never)).toBeNull()
    expect(field('cv').optional).toBe(true)
  })
})

describe('readPerk', () => {
  it('reads the guide off the URL the landing\'s button carries, and nothing else', () => {
    expect(readPerk('?perk=guida')).toBe('guida')
    expect(readPerk('?utm_source=linkedin&perk=guida')).toBe('guida')
    expect(readPerk('?perk=crm')).toBeNull()
    expect(readPerk('')).toBeNull()
  })
})

describe('FreelancerWizard', () => {
  it('says why to finish when the URL says the person came for the guide (ORB-154)', async () => {
    mount('/freelance?perk=guida')
    const note = await screen.findByRole('note', { name: 'Perché completare l’iscrizione' })
    expect(note).toHaveTextContent('Completa l’iscrizione per scaricare la guida per diventare un freelance tech.')
    expect(note).toHaveTextContent('lo trovi nella tua area appena sei dentro')
    // The wizard itself is untouched: same heading, same first question.
    expect(screen.getByRole('heading', { level: 1, name: 'Entra in rebase' })).toBeInTheDocument()
  })

  it('shows no such note to whoever arrives without the key', async () => {
    mount()
    await screen.findByRole('heading', { level: 1, name: 'Entra in rebase' })
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
  })

  it('walks the eight questions, posts the multipart body with the UTM and lands on thanks', async () => {
    const user = userEvent.setup({ applyAccept: false })
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 201 }),
    )
    const router = mount()

    await walkToReview(user)
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument()
    expect(screen.getByText('Ada CV.pdf')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Invia/ }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/thanks'))
    expect(router.state.location.search).toEqual({ chi: 'freelance' })

    const [url, init] = fetchSpy.mock.calls[0]!
    expect(url).toBe('/api/hub/freelancers')
    const body = init?.body as FormData
    expect(body.get('email')).toBe('ada@studio.it')
    expect(body.get('remoto')).toBe('remoto')
    expect(body.get('utm_source')).toBe('linkedin')
    expect(body.get('origine')).toBe('pigrocrm')
    expect(body.getAll('links')).toEqual(['https://github.com/ada'])
    expect((body.get('cv') as File).name).toBe('Ada CV.pdf')
  })

  it('posts a rate typed with Italian thousands as the number it means (REB-485)', async () => {
    const user = userEvent.setup({ applyAccept: false })
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 201 }),
    )
    const router = mount()

    await walkToReview(user, '1.500')
    expect(screen.getByText('1.500 € / giorno')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Invia/ }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/thanks'))

    const body = fetchSpy.mock.calls[0]![1]?.body as FormData
    expect(body.get('tariffa_giornaliera')).toBe('1500')
  })

  /** ORB-203: an address pasted from the phone becomes the name in the field, behind the
   *  fixed `linkedin.com/in/`, and the body carries the profile in its stored shape. */
  it('reduces a pasted LinkedIn address to the name and posts the profile', async () => {
    const user = userEvent.setup({ applyAccept: false })
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 201 }),
    )
    const router = mount()

    await user.type(await screen.findByLabelText('Nome'), 'Ada')
    await user.type(screen.getByLabelText('Cognome'), 'Lovelace{Enter}')
    await user.type(screen.getByLabelText('Email'), 'ada@studio.it{Enter}')
    const field = screen.getByLabelText('Profilo LinkedIn')
    expect(screen.getByRole('link', { name: 'Apri il tuo profilo LinkedIn' })).toHaveAttribute(
      'href',
      'https://www.linkedin.com/in/me/',
    )
    await user.click(field)
    await user.paste('https://it.linkedin.com/in/ada-lovelace/?utm_source=share')
    expect(field).toHaveValue('ada-lovelace')
    expect(screen.getByText('linkedin.com/in/')).toBeInTheDocument()
    await user.keyboard('{Enter}')
    await user.click(screen.getByRole('button', { name: /Avanti/ })) // the CV, skipped
    await user.type(screen.getByLabelText('Tariffa a giornata'), '450{Enter}')
    await user.type(screen.getByLabelText('Posizione'), 'Backend developer{Enter}')
    await user.click(screen.getByRole('radio', { name: /Da remoto/ }))
    await user.click(screen.getByRole('button', { name: /Rivedi|Avanti/ }))
    await user.click(screen.getByRole('button', { name: /Rivedi/ }))
    expect(screen.getByText('https://www.linkedin.com/in/ada-lovelace')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Invia/ }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/thanks'))
    const body = fetchSpy.mock.calls[0]![1]?.body as FormData
    expect(body.get('linkedin_url')).toBe('https://www.linkedin.com/in/ada-lovelace')
  })

  /** The CV is optional: somebody with no PDF to hand finishes the form, the body
   *  carries no `cv` part at all rather than an empty one, and the area they land in is
   *  where the file arrives later. */
  it('lets a person through with no CV, and posts a body without the part', async () => {
    const user = userEvent.setup({ applyAccept: false })
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 201 }),
    )
    const router = mount()

    await user.type(await screen.findByLabelText('Nome'), 'Ada')
    await user.type(screen.getByLabelText('Cognome'), 'Lovelace{Enter}')
    await user.type(screen.getByLabelText('Email'), 'ada@studio.it{Enter}')
    await user.keyboard('{Enter}') // LinkedIn, optional
    await user.click(screen.getByRole('button', { name: /Avanti/ })) // the CV, skipped
    await user.type(screen.getByLabelText('Tariffa a giornata'), '450{Enter}')
    await user.type(screen.getByLabelText('Posizione'), 'Backend developer{Enter}')
    await user.click(screen.getByRole('radio', { name: /Da remoto/ }))
    await user.click(screen.getByRole('button', { name: /Rivedi|Avanti/ }))
    await user.click(screen.getByRole('button', { name: /Rivedi/ }))

    expect(screen.getByRole('heading', { name: 'Tutto giusto?' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Invia/ }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/thanks'))

    const body = fetchSpy.mock.calls[0]![1]?.body as FormData
    expect(body.get('email')).toBe('ada@studio.it')
    expect(body.has('cv')).toBe(false)
  })
})

describe('what the wizard reports to PostHog (ORB-185)', () => {
  it('reports wizard_iniziato once, with the kind and the perk the URL carries', async () => {
    const user = userEvent.setup()
    // Under StrictMode the engine's step effect runs, is cleaned up and runs again on
    // mount: the guard against a second `wizard_iniziato` is what this exercises.
    mount('/freelance?perk=guida&utm_source=linkedin', { strict: true })
    const nome = await screen.findByLabelText('Nome')
    expect(captured('wizard_iniziato')).toEqual([{ tipo: 'freelance', perk: 'guida' }])
    // Started first, then the first step on screen, with the same properties: a funnel
    // reads the two in this order and never ties on the timestamps.
    expect(vi.mocked(capture).mock.calls.slice(0, 2)).toEqual([
      ['wizard_iniziato', { tipo: 'freelance', perk: 'guida' }],
      ['wizard_passo', { tipo: 'freelance', perk: 'guida', passo: 0, passi: 8, schermata: 'nome' }],
    ])

    // Typing re-renders the page; the person did not start twice.
    await user.type(nome, 'Ada')
    expect(captured('wizard_iniziato')).toHaveLength(1)
  })

  it('reports every step the person reaches, by index, and no perk when the URL has none', async () => {
    const user = userEvent.setup()
    mount('/freelance')
    await user.type(await screen.findByLabelText('Nome'), 'Ada')
    await user.type(screen.getByLabelText('Cognome'), 'Lovelace{Enter}')
    await user.type(screen.getByLabelText('Email'), 'ada@studio.it{Enter}')
    await user.click(screen.getByRole('button', { name: 'Indietro' }))
    expect(captured('wizard_passo').map((p) => p?.passo)).toEqual([0, 1, 2, 1])
    expect(captured('wizard_passo').map((p) => p?.schermata)).toEqual([
      'nome',
      'email',
      'linkedin_url',
      'email',
    ])
    expect(captured('wizard_passo')[0]).toEqual({
      tipo: 'freelance',
      passo: 0,
      passi: 8,
      schermata: 'nome',
    })
  })

  it('reports wizard_completato once the API accepted the candidacy, with the review as the last step', async () => {
    const user = userEvent.setup({ applyAccept: false })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 201 }),
    )
    const router = mount('/freelance?perk=guida')
    await walkToReview(user)
    expect(captured('wizard_passo').at(-1)).toEqual({
      tipo: 'freelance',
      perk: 'guida',
      passo: 8,
      passi: 8,
      schermata: 'riepilogo',
    })
    expect(captured('wizard_completato')).toEqual([])

    await user.click(screen.getByRole('button', { name: /Invia/ }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/thanks'))
    expect(captured('wizard_completato')).toEqual([{ tipo: 'freelance', perk: 'guida' }])
  })

  it('reports nothing completed when the API refused, and the jump back as a step', async () => {
    const user = userEvent.setup({ applyAccept: false })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({ detail: [{ loc: ['body', 'email'], msg: 'Questo indirizzo è già iscritto.' }] }),
        { status: 422 },
      ),
    )
    const router = mount()
    await walkToReview(user)
    await user.click(screen.getByRole('button', { name: /Invia/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Questo indirizzo è già iscritto.')
    expect(router.state.location.pathname).toBe('/freelance')
    expect(captured('wizard_completato')).toEqual([])
    expect(captured('wizard_passo').at(-1)).toEqual({
      tipo: 'freelance',
      passo: 1,
      passi: 8,
      schermata: 'email',
    })
  })
})

describe('FreelancerWizard, the draft and the intro (REB-215)', () => {
  it('introduces rebase above the first question, and only there', async () => {
    const user = userEvent.setup()
    mount()
    const intro = await screen.findByRole('complementary', { name: 'Cos’è rebase' })
    expect(intro).toHaveTextContent('8 domande, circa tre minuti')
    await user.type(screen.getByLabelText('Nome'), 'Ada')
    await user.type(screen.getByLabelText('Cognome'), 'Lovelace{Enter}')
    expect(screen.queryByRole('complementary', { name: 'Cos’è rebase' })).not.toBeInTheDocument()
  })

  it('keeps the answers and the step, and picks them up on the next visit', async () => {
    const user = userEvent.setup()
    mount()
    await user.type(await screen.findByLabelText('Nome'), 'Ada')
    await user.type(screen.getByLabelText('Cognome'), 'Lovelace{Enter}')
    await user.type(screen.getByLabelText('Email'), 'ada@studio.it{Enter}')
    expect(loadDraft<FreelancerApplication>(FREELANCER_DRAFT_KEY)).toMatchObject({
      index: 2,
      value: { nome: 'Ada', cognome: 'Lovelace', email: 'ada@studio.it' },
    })

    // The next visit: the same question they were on, the answers still there.
    cleanup()
    mount()
    expect(await screen.findByRole('heading', { name: /Il tuo profilo LinkedIn/ })).toBeInTheDocument()
    const note = screen.getByRole('note', { name: 'Risposte ritrovate' })
    await user.click(screen.getByRole('button', { name: 'Indietro' }))
    await user.click(screen.getByRole('button', { name: 'Indietro' }))
    expect(screen.getByLabelText('Nome')).toHaveValue('Ada')

    // Or not the same person: one click, and the form is blank and forgotten.
    await user.click(within(note).getByRole('button', { name: 'Ricomincia' }))
    expect(screen.queryByRole('note', { name: 'Risposte ritrovate' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Nome')).toHaveValue('')
    expect(loadDraft(FREELANCER_DRAFT_KEY)).toBeNull()
  })

  it('forgets the draft once the application is sent, and sends the browser id with it', async () => {
    const user = userEvent.setup({ applyAccept: false })
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 201 }),
    )
    const router = mount()
    await walkToReview(user)
    expect(loadDraft(FREELANCER_DRAFT_KEY)).not.toBeNull()
    await user.click(screen.getByRole('button', { name: /Invia/ }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/thanks'))
    expect(loadDraft(FREELANCER_DRAFT_KEY)).toBeNull()
    const body = fetchSpy.mock.calls[0]![1]?.body as FormData
    expect(body.get('distinct_id')).toBe('anon-1')
  })
})
