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
  guard_hits: [],
  decided_at: null,
  decided_by_name: null,
  decision_note: null,
  profile: {
    window_from: '2026-04',
    window_to: '2026-05',
    products: 23,
    sessions: 44,
    instructions: 2701,
    summary: 'Lavora con l’AI in modo orchestrato.',
    domains: [{ where: 'domains[0].note', label: 'Fintech', products: 4, sessions: 9, note: 'Pagamenti.' }],
    lenses: [
      {
        key: 'agenticLiteracy',
        label: 'Quanto è fluente con gli agenti',
        narrative: 'Costruisce i propri comandi.',
        where: 'agenticLiteracy.narrative',
        tags: [],
        counts: [{ label: 'Deleghe a sub-agenti', value: '70' }],
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

function open(profile: unknown = PROFILE, more: Record<string, unknown> = {}) {
  const spy = routeFetch({
    ...more,
    'GET /api/hub/freelancers/f2': COMPLETE,
    'GET /api/hub/freelancers/f2/audit': [],
    'GET /api/hub/freelancers/f2/card': noCard('f2'),
  })
  mount('/admin/freelance/f2', undefined, undefined, profile as Record<string, unknown>)
  return spy
}

describe('«Profilo di lavoro» on the talent page (REB-1126)', () => {
  it('shows nothing for a person who sent no profile', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f2': COMPLETE,
      'GET /api/hub/freelancers/f2/audit': [],
      'GET /api/hub/freelancers/f2/card': noCard('f2'),
    })
    mount('/admin/freelance/f2')
    await screen.findByRole('heading', { name: 'Grace Hopper' })
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Profilo di lavoro' })).toBeNull())
  })

  it('reads the state, the recheck and the profile, each anchor beside its own prose', async () => {
    open()
    const section = await screen.findByRole('region', { name: 'Profilo di lavoro' })
    expect(within(section).getByText('Da rivedere')).toBeInTheDocument()
    expect(within(section).getByText('1.0.0')).toBeInTheDocument()
    expect(within(section).getByText('89%')).toBeInTheDocument()
    const summary = within(section).getAllByText('Lavora con l’AI in modo orchestrato.').at(-1)!.parentElement as HTMLElement
    expect(within(summary).getByText(/«12 commits» \(numero\)/)).toBeInTheDocument()
    const project = within(section).getByText('Revisione di sicurezza.').parentElement as HTMLElement
    expect(within(project).getByText(/«Kafka» \(tecnologia\)/)).toBeInTheDocument()
    // An anchor filed under a field the page does not show is not lost.
    expect(within(section).getByLabelText('Da verificare altrove')).toHaveTextContent('p9.did')
    expect(within(section).getByText('Deleghe a sub-agenti')).toBeInTheDocument()
    expect(within(section).getByRole('link', { name: 'Demo' })).toHaveAttribute('href', 'https://example.com/demo')
    expect(within(section).getByText('Esplora prima di modificare.')).toBeInTheDocument()
  })

  it('approves with an optional note', async () => {
    const spy = open(PROFILE, {
      'POST /api/hub/freelancers/f2/work-profile/approve': {
        ...PROFILE,
        state: 'approved',
        decision_note: 'solido',
        decided_by_name: 'Ivan',
      },
    })
    const section = await screen.findByRole('region', { name: 'Profilo di lavoro' })
    await userEvent.type(within(section).getByLabelText('Nota'), 'solido')
    await userEvent.click(within(section).getByRole('button', { name: 'Approva' }))
    expect(await within(section).findByText('Approvato')).toBeInTheDocument()
    const call = spy.mock.calls.find(([url]) => String(url).endsWith('/work-profile/approve'))
    expect(JSON.parse(call?.[1]?.body as string)).toEqual({ note: 'solido', submitted_at: PROFILE.submitted_at, override: false })
    expect(within(section).queryByRole('button', { name: 'Approva' })).toBeNull()
  })

  it('refuses without a note and sends the note it rejects with', async () => {
    const spy = open(PROFILE, {
      'POST /api/hub/freelancers/f2/work-profile/reject': {
        ...PROFILE,
        state: 'rejected',
        decision_note: 'Manca un link.',
      },
    })
    const section = await screen.findByRole('region', { name: 'Profilo di lavoro' })
    const reject = within(section).getByRole('button', { name: 'Rifiuta' })
    expect(reject).toBeDisabled()
    await userEvent.type(within(section).getByLabelText('Nota'), 'Manca un link.')
    await userEvent.click(reject)
    expect(await within(section).findByText('Rifiutato')).toBeInTheDocument()
    const call = spy.mock.calls.find(([url]) => String(url).endsWith('/work-profile/reject'))
    expect(JSON.parse(call?.[1]?.body as string)).toEqual({ note: 'Manca un link.', submitted_at: PROFILE.submitted_at })
  })

  it('lists the guard hits in «Cosa leggono le aziende» and holds «Approva» until an override has a note', async () => {
    open({ ...PROFILE, guard_hits: [{ field: 'summary', rule: 'name_account_site_or_client' }] })
    const section = await screen.findByRole('region', { name: 'Profilo di lavoro' })
    const panel = within(section).getByRole('group', { name: 'Cosa leggono le aziende' })
    expect(within(panel).getByText(/Controllo di anonimato: il nome, un account/)).toBeInTheDocument()
    const approve = within(section).getByRole('button', { name: 'Approva' })
    expect(approve).toBeDisabled()
    await userEvent.click(within(section).getByRole('checkbox'))
    await userEvent.type(within(section).getByLabelText('Nota'), 'Il cliente è pubblico.')
    expect(approve).toBeEnabled()
  })

  it('shows an expired profile as expired and offers no decision', async () => {
    open({ ...PROFILE, expired: true })
    const section = await screen.findByRole('region', { name: 'Profilo di lavoro' })
    expect(within(section).getByText('Scaduto')).toBeInTheDocument()
    expect(within(section).queryByRole('button', { name: 'Approva' })).toBeNull()
  })
})
