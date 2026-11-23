import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import { COMPLETE, mount, noCard, resetMocks, routeFetch } from '../list-test-kit'

afterEach(resetMocks)

const PROFILE = {
  freelancer_id: 'f2',
  state: 'draft',
  submitted_at: '2026-11-12T10:00:00Z',
  expires_at: '2027-11-12T10:00:00Z',
  expired: false,
  tool_version: '1.0.0',
  groundedness: 89,
  unsupported_anchors: [
    { where: 'summary', anchor: '12 commits', kind: 'number' },
    { where: 'p1.did', anchor: 'Kafka', kind: 'tech' },
    { where: 'p9.did', anchor: 'Redis', kind: 'tech' },
  ],
  unknown_technologies: 0,
  unknown_practices: 0,
  guard_hits: [],
  decided_at: null,
  decided_by_name: null,
  decision_note: null,
  profile: {
    window_from: '2026-04',
    window_to: '2026-08',
    products: 23,
    sessions: 1058,
    instructions: 2701,
    summary: 'Lavora con l’AI in modo orchestrato.',
    domains: [
      { where: 'domains[0].note', label: 'Fintech', products: 4, sessions: 9, note: 'Pagamenti.' },
      { where: 'domains[1].note', label: 'Logistica', products: 2, sessions: 3, note: null },
    ],
    lenses: [
      {
        key: 'agenticLiteracy',
        label: 'Quanto è fluente con gli agenti',
        narrative: 'Costruisce i propri comandi.',
        where: 'agenticLiteracy.narrative',
        tags: [],
        counts: [{ label: 'Deleghe a sub-agenti', value: '70' }],
      },
      {
        key: 'aiRelationship',
        label: 'Come lavora con il modello',
        narrative: `${'Dirige il modello con istruzioni brevi e precise. '.repeat(8)}`,
        where: 'aiRelationship.narrative',
        tags: ['direttivo'],
        counts: [
          { label: 'Dirige', value: '70%' },
          { label: 'Ragiona insieme', value: '30%' },
        ],
      },
      {
        key: 'intensity',
        label: 'Quanto è profonda la pratica',
        narrative: null,
        where: 'intensity.narrative',
        tags: [],
        counts: [
          { label: 'Giorni osservati', value: '122' },
          { label: 'Giorni attivi', value: '118' },
        ],
      },
    ],
    trajectory: {
      narrative: 'Più delega nel tempo.',
      where: 'trajectory.narrative',
      shifts: [{ metric: 'delegation rate', early: '0.02', late: '0.05' }],
      topics: [],
      principles: [{ when: '2026-04', text: 'Esplora prima di modificare.' }],
      new_vocabulary: [],
    },
    stack: ['Zod'],
    practices: [],
    technologies: [
      { name: 'Zod', band: 'recurring', sessions: 9, products: 2, months: 4, first_month: '2026-01', last_month: '2026-05' },
      { name: 'Python', band: 'continuous', sessions: 40, products: 6, months: 6, first_month: '2026-01', last_month: '2026-06' },
      { name: 'Go', band: 'occasional', sessions: 3, products: 1, months: 1, first_month: '2026-04', last_month: '2026-04' },
    ],
    projects: [
      {
        id: 'p1',
        domain: 'Piattaforma per creator.',
        did: 'Revisione di sicurezza.',
        why_representative: 'Il più lungo.',
        tech: ['Supabase/Postgres'],
        types: ['feature-work'],
        sessions: 6,
        span_from: '2026-05',
        span_to: '2026-05',
        commits: 6,
        reverts: 0,
        artifact: { label: 'Demo', url: 'https://example.com/demo' },
      },
    ],
    other_projects: 8,
  },
}

const CARD = {
  role: 'CTO',
  seniority: 'lead',
  experience_years: 15,
  skills: ['Architettura'],
  industries: [],
  languages: ['italiano'],
  location: null,
  profile_summary: 'Guida team di prodotto da quindici anni.',
}

function open(profile: unknown = PROFILE, more: Record<string, unknown> = {}, desktop = true, card: unknown = noCard('f2')) {
  const spy = routeFetch({
    ...more,
    'GET /api/hub/freelancers/f2': COMPLETE,
    'GET /api/hub/freelancers/f2/audit': [],
    'GET /api/hub/freelancers/f2/card': card,
  })
  mount('/admin/freelance/f2', undefined, undefined, profile as Record<string, unknown>, desktop)
  return spy
}

/** The tab's own region, the profile read out of it; the rail is beside it, not in it. */
const profileTab = () => screen.findByRole('region', { name: 'Profilo di lavoro' })
const rail = () => screen.findByRole('complementary', { name: 'Decisione, stato e note' })

describe('«Profilo di lavoro» on the talent page (REB-1126, REB-1158)', () => {
  it('says how the person sends one when there is none', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f2': COMPLETE,
      'GET /api/hub/freelancers/f2/audit': [],
      'GET /api/hub/freelancers/f2/card': noCard('f2'),
    })
    mount('/admin/freelance/f2')
    await screen.findByRole('heading', { name: 'Grace Hopper' })
    await userEvent.click(await screen.findByRole('tab', { name: /^Profilo di lavoro/ }))
    const section = await profileTab()
    expect(within(section).getByText('Nessun profilo di lavoro')).toBeInTheDocument()
    expect(within(section).getByText('npx @letsrebase/verify')).toBeInTheDocument()
    expect(screen.queryByRole('complementary', { name: 'Decisione, stato e note' })).not.toHaveTextContent('Decisione sul profilo')
  })

  it('reads the figures, the recheck and the profile, each anchor beside its own prose', async () => {
    open()
    const section = await profileTab()
    const band = within(section).getByLabelText('Numeri del profilo')
    expect(band).toHaveTextContent('Sessioni1058')
    expect(band).toHaveTextContent('Prodotti23')
    expect(band).toHaveTextContent('Giorni attivi118 su 122')
    expect(band).toHaveTextContent('5 mesi')
    expect(band).toHaveTextContent('apr – ago 2026')
    expect(band).toHaveTextContent('Verificabile89%')
    const summary = within(section).getAllByText('Lavora con l’AI in modo orchestrato.').at(-1)!.parentElement as HTMLElement
    expect(within(summary).getByText(/Non trovato nei dati: «12 commits» \(numero\)/)).toBeInTheDocument()
    const project = within(section).getByText('Revisione di sicurezza.').parentElement as HTMLElement
    expect(within(project).getByText(/«Kafka» \(tecnologia\)/)).toBeInTheDocument()
    // An anchor filed under a field the page does not show is not lost.
    expect(within(section).getByLabelText('Da verificare altrove')).toHaveTextContent('p9.did')
    expect(within(section).getByText('Deleghe a sub-agenti')).toBeInTheDocument()
    expect(within(section).getByRole('link', { name: 'Demo' })).toHaveAttribute('href', 'https://example.com/demo')
    expect(within(section).getByText('Esplora prima di modificare.')).toBeInTheDocument()
    expect(within(section).getByText('Altri 8 progetti, senza testo.')).toBeInTheDocument()
  })

  it('draws the fields of work as bars with their note, and a long lens clamped with «Leggi tutto»', async () => {
    open()
    const section = await profileTab()
    const where = within(section).getByRole('heading', { name: 'Dove lavora' }).parentElement as HTMLElement
    expect(within(where).getByText('Fintech')).toBeInTheDocument()
    expect(within(where).getByText('Pagamenti.')).toBeInTheDocument()
    expect(within(where).getByText('4 prodotti · 9 sessioni')).toBeInTheDocument()
    const lens = within(section).getByRole('heading', { name: 'Come lavora con il modello' }).parentElement as HTMLElement
    const narrative = within(lens).getByText(/Dirige il modello con istruzioni brevi/)
    expect(narrative).toHaveClass('line-clamp-4')
    await userEvent.click(within(lens).getByRole('button', { name: 'Leggi tutto' }))
    expect(narrative).not.toHaveClass('line-clamp-4')
    expect(within(lens).getByRole('button', { name: 'Mostra meno' })).toHaveAttribute('aria-expanded', 'true')
    // The lens's counts are a small list beside it, never a score.
    expect(within(lens).getByText('Ragiona insieme')).toBeInTheDocument()
    expect(within(lens).getByText('30%')).toBeInTheDocument()
  })

  it('shows what a company would read, drawn by the cloud’s own card, once the card is written', async () => {
    open(PROFILE, {}, true, { ...noCard('f2'), card: CARD, mode: 'remote' })
    const section = await profileTab()
    const preview = within(section).getByRole('heading', { name: "Come lo legge un'azienda" }).parentElement as HTMLElement
    expect(await within(preview).findByRole('heading', { name: 'Grace Hopper', level: 3 })).toBeInTheDocument()
    expect(within(preview).getByText('CTO · Lead · 15 anni')).toBeInTheDocument()
    // The three fields of the cloud read, and nothing of the profile beyond them.
    expect(within(preview).getByRole('list', { name: 'Ambiti di lavoro' })).toHaveTextContent('Fintech · 9 sessioni')
    expect(within(preview).queryByText('Python')).toBeNull()
    expect(within(preview).getByText('Nessun controllo scattato')).toBeInTheDocument()
    // It files no request: «Richiedi» is off on a preview.
    expect(within(preview).getByRole('button', { name: 'Richiedi Grace Hopper' })).toBeDisabled()
  })

  it('leaves out of that card what an anonymity check named', async () => {
    open(
      { ...PROFILE, guard_hits: [{ field: 'summary', rule: 'name_account_site_or_client' }, { field: 'domains[1].label', rule: 'link_or_email' }] },
      {},
      true,
      { ...noCard('f2'), card: CARD, mode: 'remote' },
    )
    const section = await profileTab()
    const preview = within(section).getByRole('heading', { name: "Come lo legge un'azienda" }).parentElement as HTMLElement
    await within(preview).findByRole('heading', { name: 'Grace Hopper', level: 3 })
    const tags = within(within(preview).getByRole('list', { name: 'Ambiti di lavoro' })).getAllByRole('listitem')
    expect(tags.map((tag) => tag.textContent)).toEqual(['Fintech · 9 sessioni'])
    expect(within(preview).getByText(/scattato su il riassunto: il nome, un account/)).toBeInTheDocument()
    expect(within(preview).getByText(/scattato su un ambito di lavoro: un indirizzo web o una email/)).toBeInTheDocument()
  })
})

describe('the decision, in the rail (REB-1158)', () => {
  it('is reachable on the profile tab, in the rail beside it and not in a form at the foot of the page', async () => {
    open()
    const section = await profileTab()
    const aside = await rail()
    expect(within(aside).getByRole('region', { name: 'Decisione sul profilo' })).toBeInTheDocument()
    expect(within(aside).getByRole('button', { name: 'Approva' })).toBeInTheDocument()
    expect(within(aside).getByRole('button', { name: 'Rifiuta' })).toBeInTheDocument()
    // What the admin weighs is next to the buttons.
    expect(within(aside).getByText('89%')).toBeInTheDocument()
    expect(within(aside).getByText('1.0.0')).toBeInTheDocument()
    expect(within(aside).getByText('Nessun controllo scattato')).toBeInTheDocument()
    // The tab itself holds the profile to read, with no form in it.
    expect(within(section).queryByRole('button', { name: 'Approva' })).toBeNull()
    expect(within(section).queryByLabelText('Nota')).toBeNull()
  })

  it('approves with an optional note', async () => {
    const spy = open(PROFILE, {
      'POST /api/hub/freelancers/f2/work-profile/approve': {
        ...PROFILE,
        state: 'approved',
        decision_note: 'solido',
        decided_by_name: 'Ivan',
        decided_at: '2026-11-13T10:00:00Z',
      },
    })
    const aside = await rail()
    await userEvent.type(await within(aside).findByLabelText('Nota'), 'solido')
    await userEvent.click(within(aside).getByRole('button', { name: 'Approva' }))
    expect(await within(aside).findByText('Approvato')).toBeInTheDocument()
    const call = spy.mock.calls.find(([url]) => String(url).endsWith('/work-profile/approve'))
    expect(JSON.parse(call?.[1]?.body as string)).toEqual({ note: 'solido', submitted_at: PROFILE.submitted_at, override: false })
    expect(within(aside).queryByRole('button', { name: 'Approva' })).toBeNull()
    // The decision is signed in the rail's status card, with its note, and in the header.
    expect(within(aside).getByText('solido')).toBeInTheDocument()
    expect(within(aside).getByText(/da Ivan/)).toBeInTheDocument()
    expect(within(screen.getByRole('banner')).getByText('Profilo approvato')).toBeInTheDocument()
  })

  it('refuses without a note and sends the note it rejects with', async () => {
    const spy = open(PROFILE, {
      'POST /api/hub/freelancers/f2/work-profile/reject': {
        ...PROFILE,
        state: 'rejected',
        decision_note: 'Manca un link.',
      },
    })
    const aside = await rail()
    const reject = await within(aside).findByRole('button', { name: 'Rifiuta' })
    expect(reject).toBeDisabled()
    await userEvent.type(within(aside).getByLabelText('Nota'), 'Manca un link.')
    await userEvent.click(reject)
    expect(await within(aside).findByText('Rifiutato')).toBeInTheDocument()
    const call = spy.mock.calls.find(([url]) => String(url).endsWith('/work-profile/reject'))
    expect(JSON.parse(call?.[1]?.body as string)).toEqual({ note: 'Manca un link.', submitted_at: PROFILE.submitted_at })
  })

  it('lists the anonymity hits and holds «Approva» until an override has a note', async () => {
    open({ ...PROFILE, guard_hits: [{ field: 'summary', rule: 'name_account_site_or_client' }] })
    const aside = await rail()
    expect(await within(aside).findByText(/Controllo di anonimato scattato su il riassunto: il nome, un account/)).toBeInTheDocument()
    const approve = within(aside).getByRole('button', { name: 'Approva' })
    expect(approve).toBeDisabled()
    await userEvent.click(within(aside).getByRole('checkbox'))
    await userEvent.type(within(aside).getByLabelText('Nota'), 'Il cliente è pubblico.')
    expect(approve).toBeEnabled()
  })

  it('shows an expired profile as expired, with no decision to take', async () => {
    open({ ...PROFILE, expired: true })
    const aside = await rail()
    expect(await within(aside).findByText('Scaduto')).toBeInTheDocument()
    expect(within(aside).queryByRole('button', { name: 'Approva' })).toBeNull()
    expect(within(screen.getByRole('banner')).getByText('Profilo scaduto')).toBeInTheDocument()
  })

  it('shows the status of a decided profile in a compact card, with who decided and why', async () => {
    open({ ...PROFILE, state: 'rejected', decided_at: '2026-11-13T10:00:00Z', decided_by_name: 'Ivan', decision_note: 'Manca un link.' })
    const aside = await rail()
    expect(await within(aside).findByText('Rifiutato')).toBeInTheDocument()
    expect(within(aside).getByText(/13 nov 2026 da Ivan/)).toBeInTheDocument()
    expect(within(aside).getByText('Manca un link.')).toBeInTheDocument()
    expect(within(aside).queryByLabelText('Nota')).toBeNull()
  })

  it('becomes a bar at the foot of the page on a phone, and decides from a sheet', async () => {
    const spy = open(
      PROFILE,
      {
        'POST /api/hub/freelancers/f2/work-profile/approve': { ...PROFILE, state: 'approved', decided_by_name: 'Ivan' },
      },
      false,
    )
    await profileTab()
    expect(screen.queryByRole('complementary')).toBeNull()
    expect(screen.queryByLabelText('Nota')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Approva…' }))
    const sheet = await screen.findByRole('dialog', { name: 'Decisione sul profilo' })
    expect(within(sheet).getByText('89%')).toBeInTheDocument()
    await userEvent.click(within(sheet).getByRole('button', { name: 'Approva' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(spy.mock.calls.some(([url]) => String(url).endsWith('/work-profile/approve'))).toBe(true)
    // Decided: the bar is gone.
    expect(screen.queryByRole('button', { name: 'Approva…' })).toBeNull()
  })
})

describe('the usage bands in the review panel (REB-1130)', () => {
  it('shows a draft’s technologies as filled badges, strongest band first, with the legend', async () => {
    open()
    const section = await profileTab()
    const list = within(section).getByRole('list', { name: 'Tecnologie' })
    const badges = within(list).getAllByRole('listitem').map((item) => item.querySelector('[data-slot="badge"]') as HTMLElement)
    expect(badges.map((badge) => [badge.firstChild?.textContent, badge.getAttribute('data-fill')])).toEqual([
      ['Python', 'full'],
      ['Zod', 'two-thirds'],
      ['Go', 'third'],
    ])
    expect(badges[0]).toHaveTextContent('presenza continua: 40 sessioni in 6 prodotti, 6 mesi, da gennaio 2026 a giugno 2026')
    expect(within(section).getByText(/Il riempimento indica quanto spesso e con che continuità/)).toBeInTheDocument()
    expect(within(section).getByText(/Le aziende non le vedono/)).toBeInTheDocument()
  })

  it('says how many unknown technologies were dropped, and nothing when none were', async () => {
    open({ ...PROFILE, unknown_technologies: 2 })
    const section = await profileTab()
    expect(
      within(section).getByText('2 tecnologie non riconosciute da questa versione del hub sono state tolte dal profilo.'),
    ).toBeInTheDocument()
  })

  it('shows the «Come lavora» table when the profile has practices, the never-seen ones in one muted row', async () => {
    open({
      ...PROFILE,
      profile: {
        ...PROFILE.profile,
        practices: [
          { id: 'checks_before_commit', label: 'Verifica prima del commit', method: 'code', seen: 1, of: 5, products: 1, months: 4, classified_by: null },
          { id: 'asks_for_alternatives', label: 'Chiede alternative', method: 'model', seen: 0, of: 60, products: 0, months: 0, classified_by: null },
        ],
      },
    })
    const section = await profileTab()
    expect(within(section).getByRole('heading', { name: 'Come lavora' })).toBeInTheDocument()
    const table = within(section).getByRole('table', { name: 'Come lavora' })
    expect(within(table).getByText('1 su 5')).toBeInTheDocument()
    expect(within(table).getByText('Mai viste: Chiede alternative.')).toBeInTheDocument()
    expect(within(section).getAllByText(/Le vedono gli admin e la persona/).length).toBeGreaterThan(1)
  })

  it('names the agent and model on model rows only', async () => {
    open({
      ...PROFILE,
      profile: {
        ...PROFILE.profile,
        practices: [
          { id: 'checks_before_commit', label: 'Verifica prima del commit', method: 'code', seen: 1, of: 5, products: 1, months: 4, classified_by: null },
          { id: 'asks_for_alternatives', label: 'Chiede alternative', method: 'model', seen: 11, of: 60, products: 4, months: 5, classified_by: { agent: 'omp', model: 'claude-opus-4' } },
        ],
      },
    })
    const section = await profileTab()
    expect(within(section).getByText('dal modello della persona, non ricontrollato · oh-my-pi, claude-opus-4')).toBeInTheDocument()
    expect(within(section).getByText('contato dal codice')).toBeInTheDocument()
  })

  it('hides «Come lavora» when there are no practices', async () => {
    open(PROFILE)
    const section = await profileTab()
    expect(within(section).queryByRole('heading', { name: 'Come lavora' })).toBeNull()
  })

  it('shows one muted line for 1 and for 3 unknown practices', async () => {
    open({ ...PROFILE, unknown_practices: 1 })
    const one = await profileTab()
    expect(within(one).getByText('1 pratica non riconosciuta da questa versione del hub è stata tolta dal profilo.')).toBeInTheDocument()
  })

  it('shows the plural line for 3 unknown practices', async () => {
    open({ ...PROFILE, unknown_practices: 3 })
    const three = await profileTab()
    expect(within(three).getByText('3 pratiche non riconosciute da questa versione del hub sono state tolte dal profilo.')).toBeInTheDocument()
  })

  it('shows no dropped-technologies line when none were dropped', async () => {
    open(PROFILE)
    const section = await profileTab()
    expect(within(section).queryByText(/non riconosciut/)).toBeNull()
  })

  it('shows no block for a profile that sent none', async () => {
    open({ ...PROFILE, profile: { ...PROFILE.profile, technologies: [] } })
    const section = await profileTab()
    expect(within(section).queryByRole('list', { name: 'Tecnologie' })).toBeNull()
    expect(within(section).queryByRole('heading', { name: 'Tecnologie' })).toBeNull()
  })
})
