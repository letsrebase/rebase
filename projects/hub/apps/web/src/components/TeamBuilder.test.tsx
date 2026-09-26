import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi, type MockInstance } from 'vitest'
import type { TeamProposal } from '@/lib/api'
import { TeamBuilder } from './TeamBuilder'

// Escapes on purpose: the space before «€» is a non-breaking one, the dash an en dash.
// `toHaveTextContent` folds every run of whitespace into one plain space, the
// non-breaking one included, so a band is compared on the raw `textContent`.
const NBSP = '\u00a0'
const DASH = '\u2013'

const DESCRIZIONE =
  'Ci serve una web app per i clienti: pagamenti, dashboard dei conti, API di open banking.'

/** A public read as the API answers it, plus what it must never show: the fixture
 *  carries a freelancer id, a name and a place, so a page that rendered any of them
 *  fails here rather than in front of a visitor. */
const PROPOSAL = {
  id: '5b1f2c3d-4e5f-4a6b-8c7d-00000000abcd',
  riassunto: 'Una web app per i clienti di una fintech: chi fa il backend e chi il frontend.',
  luogo: { locale: false, dove: 'Brescia' },
  team: [
    {
      posizione: 1,
      freelancer_id: '9d0c1a2b-1111-4111-8111-000000000001',
      nome: 'Ada',
      cognome: 'Lovelace',
      ruolo: 'Backend developer',
      motivazione: 'Ha costruito le API di pagamento di due banche.',
      giorni_settimana: 5,
      scheda: {
        ruolo: 'Sviluppatore backend',
        seniority: 'senior',
        anni: 9,
        competenze: ['Python', 'FastAPI', 'PostgreSQL'],
        settori: ['fintech'],
        lingue: ['italiano', 'inglese'],
        luogo: null,
        sintesi: 'Nove anni su sistemi di pagamento, dal disegno delle API al rilascio.',
      },
      modalita: 'remoto',
      fascia: { min: 400, max: 500 },
    },
    {
      posizione: 2,
      freelancer_id: '9d0c1a2b-2222-4222-8222-000000000002',
      nome: 'Grace',
      cognome: 'Hopper',
      ruolo: 'Frontend developer',
      motivazione: 'Porta React e la cura dell’accessibilità.',
      giorni_settimana: 3,
      scheda: {
        ruolo: 'Sviluppatrice frontend',
        seniority: 'mid',
        anni: 1,
        competenze: ['React', 'TypeScript'],
        settori: [],
        lingue: ['italiano'],
        luogo: null,
        sintesi: 'Interfacce in React per prodotti rivolti ai clienti finali.',
      },
      modalita: 'ibrido',
      fascia: { min: 500, max: 650 },
    },
  ],
  economia: { giorno: { min: 900, max: 1150 }, mese: { min: 19800, max: 25300 }, giorni_mese: 22 },
  previous_id: null,
  origine: 'pubblico',
  created_at: '2026-09-26T08:00:00Z',
}

const REGENERATED = {
  ...PROPOSAL,
  id: '5b1f2c3d-4e5f-4a6b-8c7d-00000000beef',
  riassunto: 'La stessa web app, senza il frontend: basta chi fa il backend.',
  team: [PROPOSAL.team[0]],
  economia: { giorno: { min: 400, max: 500 }, mese: { min: 8800, max: 11000 }, giorni_mese: 22 },
  previous_id: PROPOSAL.id,
}

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function sent(spy: MockInstance<typeof fetch>, index: number) {
  const [url, init] = spy.mock.calls[index] as [string, RequestInit]
  return { url, method: init.method, body: JSON.parse(init.body as string) }
}

/** The builder with a proposal on screen, for the tests that start from one. */
async function proposed(fetchSpy: MockInstance<typeof fetch>, proposal: unknown = PROPOSAL) {
  const user = userEvent.setup()
  fetchSpy.mockResolvedValueOnce(answer(200, proposal))
  render(<TeamBuilder mode="public" />)
  await user.type(screen.getByLabelText('Descrizione del progetto'), DESCRIZIONE)
  await user.click(screen.getByRole('button', { name: 'Proponi il team' }))
  await screen.findByRole('heading', { name: 'La nostra proposta' })
  return user
}

afterEach(() => vi.restoreAllMocks())

describe('TeamBuilder, the box', () => {
  it('fills the box with each of the three examples', async () => {
    const user = userEvent.setup()
    render(<TeamBuilder mode="public" />)
    const box = screen.getByLabelText('Descrizione del progetto')

    const value = () => (box as HTMLTextAreaElement).value
    await user.click(screen.getByRole('button', { name: 'Web app per una fintech' }))
    expect(value()).toContain('fintech')
    // Each example is a description the API takes as it stands: forty characters at least.
    expect(value().length).toBeGreaterThanOrEqual(40)
    await user.click(screen.getByRole('button', { name: 'Pipeline dati in sede a Milano' }))
    expect(value()).toContain('Milano')
    expect(value()).not.toContain('fintech')
    expect(value().length).toBeGreaterThanOrEqual(40)
    await user.click(screen.getByRole('button', { name: 'App mobile con un designer' }))
    expect(value()).toContain('designer')
    expect(value().length).toBeGreaterThanOrEqual(40)
  })

  it('asks for forty characters before it calls anyone', async () => {
    const user = userEvent.setup()
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    render(<TeamBuilder mode="public" />)
    await user.type(screen.getByLabelText('Descrizione del progetto'), '   Un sito web.   ')
    await user.click(screen.getByRole('button', { name: 'Proponi il team' }))

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Raccontaci qualcosa in più: servono almeno 40 caratteri.',
    )
    expect(screen.getByLabelText('Descrizione del progetto')).toHaveAttribute('aria-invalid', 'true')
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('runs the proposal with the description and says it is reading the profiles meanwhile', async () => {
    const user = userEvent.setup()
    let resolve!: (response: Response) => void
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockReturnValueOnce(new Promise<Response>((done) => (resolve = done)))
    render(<TeamBuilder mode="public" />)
    await user.type(screen.getByLabelText('Descrizione del progetto'), `  ${DESCRIZIONE}  `)
    await user.click(screen.getByRole('button', { name: 'Proponi il team' }))

    expect(await screen.findByRole('button', { name: 'Sto leggendo i profili…' })).toBeDisabled()
    expect(sent(fetchSpy, 0)).toEqual({
      url: '/api/hub/team/proposals',
      method: 'POST',
      body: { descrizione: DESCRIZIONE },
    })

    resolve(answer(200, PROPOSAL))
    expect(await screen.findByText(PROPOSAL.riassunto)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Proponi il team' })).toBeEnabled()
  })
})

describe('TeamBuilder, the result', () => {
  it('shows each person and the team’s bands, and no name, no id, no place', async () => {
    await proposed(vi.spyOn(globalThis, 'fetch'))

    const team = screen.getByRole('list', { name: 'Il team' })
    const people = within(team).getAllByRole('listitem')
    expect(people).toHaveLength(2)

    const backend = people[0]!
    expect(within(backend).getByRole('heading', { name: 'Backend developer' })).toBeInTheDocument()
    expect(backend).toHaveTextContent('Sviluppatore backend, senior, 9 anni di esperienza')
    expect(backend).toHaveTextContent('Ha costruito le API di pagamento di due banche.')
    expect(backend).toHaveTextContent(PROPOSAL.team[0]!.scheda.sintesi)
    for (const skill of ['Python', 'FastAPI', 'PostgreSQL']) {
      expect(within(backend).getByText(skill)).toHaveAttribute('data-slot', 'badge')
    }
    expect(backend).toHaveTextContent('Da remoto')
    expect(backend.textContent).toContain(`400${DASH}500${NBSP}€ al giorno`)

    const frontend = people[1]!
    expect(frontend).toHaveTextContent('Sviluppatrice frontend, mid, 1 anno di esperienza')
    expect(frontend).toHaveTextContent('Ibrido')
    expect(frontend.textContent).toContain(`500${DASH}650${NBSP}€ al giorno`)

    const cost = screen.getByRole('region', { name: 'Quanto costa il team' })
    expect(cost.textContent).toContain(`900${DASH}1.150${NBSP}€ al giorno`)
    expect(cost.textContent).toContain(`19.800${DASH}25.300${NBSP}€ al mese`)
    expect(cost).toHaveTextContent('22 giorni al mese')

    const page = document.body.textContent ?? ''
    for (const secret of [
      PROPOSAL.id,
      PROPOSAL.team[0]!.freelancer_id,
      PROPOSAL.team[1]!.freelancer_id,
      'Lovelace',
      'Hopper',
      'Brescia',
    ]) {
      expect(page).not.toContain(secret)
    }
  })

  it('says «tariffa da definire» for a person with no band, and for the team', async () => {
    await proposed(vi.spyOn(globalThis, 'fetch'), {
      ...PROPOSAL,
      team: [PROPOSAL.team[0], { ...PROPOSAL.team[1], fascia: null, modalita: null }],
      economia: { giorno: null, mese: null, giorni_mese: 22 },
    })
    const people = within(screen.getByRole('list', { name: 'Il team' })).getAllByRole('listitem')
    expect(people[1]).toHaveTextContent('Tariffa da definire')
    const cost = screen.getByRole('region', { name: 'Quanto costa il team' })
    expect(cost).toHaveTextContent('Tariffa da definire')
    expect(cost).not.toHaveTextContent('al giorno')
  })

  it('shows only the summary when nobody fits: no cards, no bands, no «Assumi team»', async () => {
    const summary = 'Al momento nessun profilo corrisponde alla richiesta.'
    await proposed(vi.spyOn(globalThis, 'fetch'), {
      ...PROPOSAL,
      riassunto: summary,
      team: [],
      economia: { giorno: null, mese: null, giorni_mese: 22 },
    })
    expect(screen.getByText(summary)).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Il team' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Quanto costa il team' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Assumi team' })).toBeNull()
    expect(document.body.textContent).not.toContain('al giorno')
  })

  it('«Rigenera» sends the proposal it replaces and the note, and shows the new one', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    const user = await proposed(fetchSpy)
    fetchSpy.mockResolvedValueOnce(answer(200, REGENERATED))

    await user.type(screen.getByLabelText('Cosa cambieresti?'), '  togli il frontend  ')
    await user.click(screen.getByRole('button', { name: 'Rigenera' }))

    expect(await screen.findByText(REGENERATED.riassunto)).toBeInTheDocument()
    expect(sent(fetchSpy, 1)).toEqual({
      url: '/api/hub/team/proposals',
      method: 'POST',
      body: { descrizione: DESCRIZIONE, nota: 'togli il frontend', previous_id: PROPOSAL.id },
    })
    expect(within(screen.getByRole('list', { name: 'Il team' })).getAllByRole('listitem')).toHaveLength(1)
    expect(screen.getByRole('region', { name: 'Quanto costa il team' }).textContent).toContain(
      `8.800${DASH}11.000${NBSP}€ al mese`,
    )
    expect(screen.getByLabelText('Cosa cambieresti?')).toHaveValue('')
  })
})

describe('TeamBuilder, «Assumi team» on the public page', () => {
  it('opens the three fields, checks them with the wizard’s words, files the request and thanks', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    const user = await proposed(fetchSpy)
    expect(screen.queryByLabelText('Azienda')).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Assumi team' }))
    await user.click(screen.getByRole('button', { name: 'Invia la richiesta' }))
    expect(screen.getByText('Serve il nome dell’azienda.')).toBeInTheDocument()
    expect(screen.getByText('Serve un indirizzo email valido.')).toBeInTheDocument()
    expect(screen.getByText('Serve un numero di telefono.')).toBeInTheDocument()
    expect(screen.getByLabelText('Email')).toHaveAttribute('aria-invalid', 'true')
    expect(fetchSpy).toHaveBeenCalledTimes(1)

    let resolve!: (response: Response) => void
    fetchSpy.mockReturnValueOnce(new Promise<Response>((done) => (resolve = done)))
    await user.type(screen.getByLabelText('Azienda'), ' ACME Srl ')
    await user.type(screen.getByLabelText('Email'), 'ada@acme.it')
    await user.type(screen.getByLabelText('Telefono'), '+39 345 1234567')
    await user.click(screen.getByRole('button', { name: 'Invia la richiesta' }))
    expect(await screen.findByRole('button', { name: 'Invio…' })).toBeDisabled()
    expect(sent(fetchSpy, 1)).toEqual({
      url: '/api/hub/team/requests',
      method: 'POST',
      body: {
        proposal_id: PROPOSAL.id,
        azienda: 'ACME Srl',
        email: 'ada@acme.it',
        telefono: '+39 345 1234567',
      },
    })

    resolve(answer(201, { id: 'a1b2c3d4-0000-4000-8000-000000000001' }))
    expect(await screen.findByRole('status')).toHaveTextContent(
      'Grazie: ti scriviamo entro due giorni lavorativi.',
    )
    expect(screen.queryByLabelText('Azienda')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Assumi team' })).toBeNull()
  })
})

describe('TeamBuilder, what the API refuses', () => {
  it.each([
    [503, 'Il team builder è spento.'],
    [503, 'Troppe richieste in questo momento: riprova tra un minuto.'],
    [502, 'Non riesco a proporre un team adesso: riprova tra poco.'],
  ])('shows the %i the API answered in its own words: %s', async (status, detail) => {
    const user = userEvent.setup()
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(answer(status, { detail }))
    render(<TeamBuilder mode="public" />)
    await user.type(screen.getByLabelText('Descrizione del progetto'), DESCRIZIONE)
    await user.click(screen.getByRole('button', { name: 'Proponi il team' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(detail)
    expect(screen.getByRole('alert').textContent).toBe(detail)
  })

  it('shows the 422 «Rigenera» got in the field’s own words, and keeps the proposal', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    const user = await proposed(fetchSpy)
    const reason = 'Questa proposta non esiste o è scaduta: chiedi di nuovo il team.'
    fetchSpy.mockResolvedValueOnce(
      answer(422, { detail: [{ loc: ['body', 'previous_id'], msg: reason, type: 'value_error' }] }),
    )
    await user.click(screen.getByRole('button', { name: 'Rigenera' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(reason)
    expect(screen.getByText(PROPOSAL.riassunto)).toBeInTheDocument()
  })

  it('shows the 409 of a proposal already requested', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    const user = await proposed(fetchSpy)
    await user.click(screen.getByRole('button', { name: 'Assumi team' }))
    await user.type(screen.getByLabelText('Azienda'), 'ACME Srl')
    await user.type(screen.getByLabelText('Email'), 'ada@acme.it')
    await user.type(screen.getByLabelText('Telefono'), '+39 345 1234567')
    fetchSpy.mockResolvedValueOnce(answer(409, { detail: 'Questa proposta è già stata richiesta.' }))
    await user.click(screen.getByRole('button', { name: 'Invia la richiesta' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Questa proposta è già stata richiesta.')
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('puts a 422 on a contact under that field, and one on the proposal above the button', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    const user = await proposed(fetchSpy)
    await user.click(screen.getByRole('button', { name: 'Assumi team' }))
    await user.type(screen.getByLabelText('Azienda'), 'ACME Srl')
    await user.type(screen.getByLabelText('Email'), 'ada@acme.it')
    await user.type(screen.getByLabelText('Telefono'), '+39 345 1234567')

    fetchSpy.mockResolvedValueOnce(
      answer(422, { detail: [{ loc: ['body', 'email'], msg: 'Questo indirizzo non riceve posta.' }] }),
    )
    await user.click(screen.getByRole('button', { name: 'Invia la richiesta' }))
    const onEmail = await screen.findByText('Questo indirizzo non riceve posta.')
    expect(screen.getByLabelText('Email')).toHaveAttribute('aria-describedby', onEmail.id)

    const reason = 'Questa proposta non esiste o è scaduta: chiedi di nuovo il team.'
    fetchSpy.mockResolvedValueOnce(
      answer(422, { detail: [{ loc: ['body', 'proposal_id'], msg: reason }] }),
    )
    await user.click(screen.getByRole('button', { name: 'Invia la richiesta' }))
    expect(await screen.findByText(reason)).toHaveAttribute('role', 'alert')
    expect(screen.queryByText('Questo indirizzo non riceve posta.')).toBeNull()
  })

  it('has a sentence of its own only when nothing answered at all', async () => {
    const user = userEvent.setup()
    vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new TypeError('Failed to fetch'))
    render(<TeamBuilder mode="public" />)
    await user.type(screen.getByLabelText('Descrizione del progetto'), DESCRIZIONE)
    await user.click(screen.getByRole('button', { name: 'Proponi il team' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Non siamo riusciti a proporre un team. Riprova.',
    )
  })
})

describe('TeamBuilder in the cloud', () => {
  it('proposes through the cloud’s own call and files «Assumi team» at once, with no form', async () => {
    const user = userEvent.setup()
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    const propose = vi.fn().mockResolvedValue(PROPOSAL as unknown as TeamProposal)
    const hire = vi.fn().mockResolvedValue({ id: 'a1b2c3d4-0000-4000-8000-000000000001' })
    render(<TeamBuilder mode="cloud" propose={propose} hire={hire} />)
    await user.type(screen.getByLabelText('Descrizione del progetto'), DESCRIZIONE)
    await user.click(screen.getByRole('button', { name: 'Proponi il team' }))
    await screen.findByText(PROPOSAL.riassunto)
    expect(propose).toHaveBeenCalledWith({ descrizione: DESCRIZIONE })

    await user.click(screen.getByRole('button', { name: 'Assumi team' }))
    await waitFor(() => expect(hire).toHaveBeenCalledWith(PROPOSAL.id))
    expect(screen.queryByLabelText('Azienda')).toBeNull()
    expect(await screen.findByRole('status')).toHaveTextContent(
      'Grazie: ti scriviamo entro due giorni lavorativi.',
    )
    expect(fetchSpy).not.toHaveBeenCalled()
  })
})
