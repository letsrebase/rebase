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
import { readOrigin } from '@/lib/utm'
import { STALE_SENTENCE, Team, UNREACHABLE_SENTENCE } from './Team'

const DESCRIZIONE =
  'Ci serve una web app per i clienti: pagamenti, dashboard dei conti, API di open banking.'

/** A public read as `GET /api/hub/team/proposals/{id}` answers it (REB-675): two people,
 *  sized by the engine (`persone` null), the way the landing asks. */
const PROPOSAL = {
  id: '5b1f2c3d-4e5f-4a6b-8c7d-00000000abcd',
  descrizione: DESCRIZIONE,
  persone: null,
  riassunto: 'Una web app per i clienti di una fintech: chi fa il backend e chi il frontend.',
  luogo: { locale: false, dove: null },
  team: [1, 2].map((posizione) => ({
    posizione,
    freelancer_id: null,
    nome: null,
    cognome: null,
    ruolo: posizione === 1 ? 'Backend developer' : 'Frontend developer',
    motivazione: 'Ha costruito le API di pagamento di due banche.',
    giorni_settimana: 5,
    scheda: {
      ruolo: 'Sviluppatore backend',
      seniority: 'senior',
      anni: 9,
      competenze: ['Python'],
      settori: [],
      lingue: ['italiano'],
      luogo: null,
      sintesi: 'Nove anni su sistemi di pagamento.',
    },
    modalita: 'remoto',
    fascia: { min: 400, max: 500 },
  })),
  economia: { giorno: { min: 800, max: 1000 }, mese: { min: 17600, max: 22000 }, giorni_mese: 22 },
  previous_id: null,
  origine: 'pubblico',
  created_at: '2026-10-07T08:00:00Z',
}

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** The page on a little router of its own, so the beta box's link has a wizard to open;
 *  `entry` carries the search the real route validates the same way (`router.tsx`). */
function mount(entry = '/team') {
  const root = createRootRoute({ component: () => <Outlet /> })
  const team = createRoute({
    getParentRoute: () => root,
    path: '/team',
    validateSearch: (
      search: Record<string, unknown>,
    ): { proposta?: string; descrizione?: string; persone?: number } => ({
      ...(typeof search.proposta === 'string' && search.proposta ? { proposta: search.proposta } : {}),
      ...(typeof search.descrizione === 'string' && search.descrizione ? { descrizione: search.descrizione } : {}),
      ...(typeof search.persone === 'number' ? { persone: search.persone } : {}),
    }),
    component: Team,
  })
  const companies = createRoute({
    getParentRoute: () => root,
    path: '/companies',
    component: () => <h1>Cerchi persone</h1>,
  })
  const router = createRouter({
    routeTree: root.addChildren([team, companies]),
    history: createMemoryHistory({ initialEntries: [entry] }),
  })
  render(<RouterProvider router={router} />)
  return router
}

afterEach(() => vi.restoreAllMocks())

describe('the public team page', () => {
  it('asks for the project under its heading, with the builder’s box and button', async () => {
    mount()
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Descrivi il progetto, ti proponiamo il team' }),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Descrizione del progetto')).toHaveAttribute(
      'placeholder',
      'Descrivi il progetto: cosa va fatto, per quanto tempo, dove, con che tecnologie',
    )
    expect(screen.getByRole('button', { name: 'Proponi il team' })).toBeInTheDocument()
  })

  it('ends on the beta box, whose button opens the company wizard with da=team-builder', async () => {
    const user = userEvent.setup()
    const router = mount()
    const beta = await screen.findByRole('complementary', { name: 'Talent cloud' })
    expect(beta).toHaveTextContent(
      "Il team builder è in beta e senza limiti. Con il talent cloud apri una posizione e la ricerca non si ferma: per tutta la durata dell'abbonamento cerchiamo nella community e fuori e ti portiamo i profili che passano la selezione. Dentro, vedi i profili per nome e chiedi i talenti direttamente.",
    )
    const link = screen.getByRole('link', { name: 'Chiedi l’accesso al talent cloud' })
    expect(link).toHaveAttribute('href', '/companies?da=team-builder')

    await user.click(link)
    await waitFor(() => expect(router.state.location.pathname).toBe('/companies'))
    // The wizard reads its origin with `readOrigin`: the parameter must be the one it reads.
    expect(readOrigin(router.state.location.searchStr)).toBe('team-builder')
  })
})

describe('the public team page opened on a proposal (REB-675)', () => {
  it('reads ?proposta= back and shows it as if just proposed, sized as the visitor saw it', async () => {
    const user = userEvent.setup()
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(answer(200, PROPOSAL))
    mount(`/team?proposta=${PROPOSAL.id}`)

    expect(await screen.findByRole('status')).toHaveTextContent('Apro la proposta…')
    expect(await screen.findByRole('heading', { name: 'La nostra proposta' })).toBeInTheDocument()
    expect(fetchSpy.mock.calls[0]?.[0]).toBe(`/api/hub/team/proposals/${PROPOSAL.id}`)
    expect(screen.queryByRole('status')).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByLabelText('Descrizione del progetto')).toHaveValue(DESCRIZIONE)
    expect(screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent)).toEqual([
      'Backend developer',
      'Frontend developer',
      'Quanto costa il team',
    ])
    // The engine sized this one (`persone` null): the selector says two, the team's own
    // size, so «Rigenera» keeps it rather than shrinking the team to one.
    expect(screen.getByRole('combobox', { name: 'Quante persone' })).toHaveTextContent('2 persone')
    expect(screen.getByRole('button', { name: 'Assumi team' })).toBeInTheDocument()

    fetchSpy.mockResolvedValueOnce(answer(200, { ...PROPOSAL, id: '5b1f2c3d-4e5f-4a6b-8c7d-00000000beef' }))
    await user.click(screen.getByRole('button', { name: 'Rigenera' }))
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(2))
    const [, init] = fetchSpy.mock.calls[1] as [string, RequestInit]
    expect(JSON.parse(init.body as string)).toEqual({
      descrizione: DESCRIZIONE,
      persone: 2,
      previous_id: PROPOSAL.id,
    })
  })

  it('says the proposal is gone over an empty builder when the read is a 404', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      answer(404, { detail: 'team_proposal 00000000-0000-7000-8000-000000000000 non trovato' }),
    )
    mount('/team?proposta=00000000-0000-7000-8000-000000000000')

    expect(await screen.findByRole('alert')).toHaveTextContent(STALE_SENTENCE)
    expect(screen.getByLabelText('Descrizione del progetto')).toHaveValue('')
    expect(screen.queryByRole('heading', { name: 'La nostra proposta' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Proponi il team' })).toBeEnabled()
  })

  it('says the read failed, not that the proposal is gone, on anything but a 404, and reads again on «Riprova»', async () => {
    const user = userEvent.setup()
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(answer(503, { detail: 'Troppe richieste in questo momento: riprova tra un minuto.' }))
      .mockResolvedValueOnce(answer(200, PROPOSAL))
    mount(`/team?proposta=${PROPOSAL.id}`)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(UNREACHABLE_SENTENCE)
    expect(alert).not.toHaveTextContent(STALE_SENTENCE)
    expect(screen.getByLabelText('Descrizione del progetto')).toHaveValue('')

    await user.click(screen.getByRole('button', { name: 'Riprova' }))
    expect(await screen.findByRole('heading', { name: 'La nostra proposta' })).toBeInTheDocument()
    expect(fetchSpy).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByLabelText('Descrizione del progetto')).toHaveValue(DESCRIZIONE)
  })

  it('fills the box from ?descrizione=, the landing’s form without JavaScript, and asks nothing', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    mount(`/team?descrizione=${encodeURIComponent(DESCRIZIONE)}&persone=3`)

    expect(await screen.findByLabelText('Descrizione del progetto')).toHaveValue(DESCRIZIONE)
    // And the headcount the same form sent, so the page asks what the landing would have.
    expect(screen.getByRole('combobox', { name: 'Quante persone' })).toHaveTextContent('3 persone')
    expect(screen.queryByRole('status')).toBeNull()
    expect(screen.queryByRole('heading', { name: 'La nostra proposta' })).toBeNull()
    expect(fetchSpy).not.toHaveBeenCalled()
  })
})
