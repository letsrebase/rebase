import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdminCampaign } from './Campaign'

const DETAIL = {
  campaign: {
    id: 'c1', name: 'Manca il CV', slug: 's', source: 'status', journey_status: 'missing_cv', filters: null, follows_id: null, subject: 'o', text: 't',
    button_text: 'b', button_meta: 'area', action: 'cv', status: 'scheduled', content_at: '2026-09-25T07:00:00Z',
    scheduled_for: '2026-09-26T07:30:00Z', test_sent_at: '2026-09-25T07:05:00Z', sent_at: null,
    created_at: '2026-09-25T07:00:00Z', stalled_at: null, stalled_reason: null, ready: true,
  },
  counts: { recipients: 2, queued: 1, sent: 0, skipped: 1, failed: 0, delivered: 0, bounced: 0, clicked: 0, income: 0, actions: 0 },
  recipients: [
    { id: 'r1', email: 'ada@studio.it', name: 'Ada', kind: 'freelancer', status: 'queued', reason: null, sent_at: null, delivered_at: null, bounced_at: null, first_click_at: null, complaint_at: null, signed_in_at: null, action_at: null, signed_in_from_mail: false, action_from_mail: false },
    { id: 'r2', email: 'bob@studio.it', name: 'Bob', kind: 'freelancer', status: 'skipped', reason: 'ha già fatto l’azione', sent_at: null, delivered_at: null, bounced_at: null, first_click_at: null, complaint_at: null, signed_in_at: null, action_at: null, signed_in_from_mail: false, action_from_mail: false },
  ],
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function mount() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const one = createRoute({ getParentRoute: () => signedIn, path: '/admin/campaigns/$id', component: AdminCampaign })
  const edit = createRoute({ getParentRoute: () => signedIn, path: '/admin/campaigns/$id/edit', component: () => <p>modifica</p> })
  const list = createRoute({ getParentRoute: () => signedIn, path: '/admin/campaigns', component: () => <p>elenco</p> })
  const router = createRouter({
    routeTree: root.addChildren([signedIn.addChildren([one, edit, list])]),
    history: createMemoryHistory({ initialEntries: ['/admin/campaigns/c1'] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('the campaign page', () => {
  it('shows the numbers, each person with the reason a row was skipped, and the scheduled actions', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(DETAIL))
    mount()
    const bob = (await screen.findByText('bob@studio.it')).closest('tr')!
    expect(within(bob).getByText('Saltata')).toBeInTheDocument()
    expect(within(bob).getByText('ha già fatto l’azione')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Riporta in bozza' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Annulla' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Modifica' })).not.toBeInTheDocument()
  })

  it('adds a person to «Non scrivere mai» from their row, after a second click like «Annulla»', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) =>
      String(input).endsWith('/never-write') ? json({ ok: true }) : json(DETAIL),
    )
    mount()
    const ada = (await screen.findByText('ada@studio.it')).closest('tr')!
    await userEvent.click(within(ada).getByRole('button', { name: 'Non scrivere mai' }))
    expect(within(ada).getByText('Non scrivere più a questa persona?')).toBeInTheDocument()
    await userEvent.click(within(ada).getByRole('button', { name: 'Indietro' }))
    expect(fetch.mock.calls.some(([url]) => String(url).endsWith('/never-write'))).toBe(false)
    await userEvent.click(within(ada).getByRole('button', { name: 'Non scrivere mai' }))
    await userEvent.click(within(ada).getByRole('button', { name: 'Conferma' }))
    expect(await within(ada).findByText('Non riceverà più campagne')).toBeInTheDocument()
    const call = fetch.mock.calls.find(([url]) => String(url).endsWith('/never-write'))!
    expect(JSON.parse(String(call[1]!.body))).toEqual({ email: 'ada@studio.it' })
  })

  it('says when a scheduled campaign leaves, in Rome time', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(DETAIL))
    mount()
    expect(await screen.findByText('Parte il 26 settembre 2026 alle 09:30 (ora di Roma)')).toBeInTheDocument()
  })

  it('says when a sent campaign left, in Rome time', async () => {
    const sent = { ...DETAIL, campaign: { ...DETAIL.campaign, status: 'sent', sent_at: '2026-09-26T07:31:00Z' } }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(sent))
    mount()
    expect(await screen.findByText('Inviata il 26 settembre 2026 alle 09:31')).toBeInTheDocument()
    expect(screen.queryByText(/^Parte il/)).not.toBeInTheDocument()
  })
})

const SENT = {
  campaign: { ...DETAIL.campaign, status: 'sent', sent_at: '2026-09-26T07:31:00Z', action: 'cv' },
  counts: { recipients: 3, queued: 0, sent: 3, skipped: 0, failed: 0, delivered: 3, bounced: 0, clicked: 2, income: 2, actions: 1 },
  recipients: [
    { id: 'r1', email: 'ada@studio.it', name: 'Ada', kind: 'freelancer', status: 'sent', reason: null, sent_at: '2026-09-26T07:31:00Z', delivered_at: '2026-09-26T07:32:00Z', bounced_at: null, first_click_at: '2026-09-26T08:00:00Z', complaint_at: null, signed_in_at: '2026-09-26T08:01:00Z', action_at: '2026-09-26T08:10:00Z', signed_in_from_mail: true, action_from_mail: false },
    { id: 'r2', email: 'bob@studio.it', name: 'Bob', kind: 'freelancer', status: 'sent', reason: null, sent_at: '2026-09-26T07:31:00Z', delivered_at: '2026-09-26T07:32:00Z', bounced_at: null, first_click_at: '2026-09-26T09:00:00Z', complaint_at: null, signed_in_at: '2026-09-26T09:01:00Z', action_at: null, signed_in_from_mail: false, action_from_mail: false },
    { id: 'r3', email: 'cleo@studio.it', name: 'Cleo', kind: 'freelancer', status: 'sent', reason: null, sent_at: '2026-09-26T07:31:00Z', delivered_at: '2026-09-26T07:32:00Z', bounced_at: null, first_click_at: null, complaint_at: null, signed_in_at: null, action_at: null, signed_in_from_mail: false, action_from_mail: false },
  ],
}

describe('the outcome of a sent campaign (phase 2)', () => {
  it('shows each figure with its share of the mails sent', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(SENT))
    mount()
    const clicks = (await screen.findByText('Cliccate')).closest('div')!
    expect(clicks).toHaveTextContent('2')
    expect(clicks).toHaveTextContent('67%')
    const done = screen.getByText('Hanno caricato il CV').closest('div')!
    expect(done).toHaveTextContent('1')
    expect(done).toHaveTextContent('33%')
  })

  it('shows Saltate as a share of the list, not of the mails sent, since a skipped row never left', async () => {
    const withSkips = { ...SENT, counts: { ...SENT.counts, recipients: 4, skipped: 1 } }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(withSkips))
    mount()
    const skipped = (await screen.findByText('Saltate')).closest('div')!
    expect(skipped).toHaveTextContent('1')
    expect(skipped).toHaveTextContent('25%')
  })

  it('says «dalla mail» where the mail was the door', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(SENT))
    mount()
    const ada = (await screen.findByText('ada@studio.it')).closest('tr')!
    expect(within(ada).getAllByText('dalla mail')).toHaveLength(1)
  })

  it('filters who did the action and who did nothing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(SENT))
    mount()
    await screen.findByText('ada@studio.it')
    await userEvent.click(screen.getByRole('button', { name: 'Non ha fatto niente (2)' }))
    expect(screen.queryByText('ada@studio.it')).not.toBeInTheDocument()
    expect(screen.getByText('bob@studio.it')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Ha fatto l’azione (1)' }))
    expect(screen.getByText('ada@studio.it')).toBeInTheDocument()
    expect(screen.queryByText('cleo@studio.it')).not.toBeInTheDocument()
  })

  it('«Riscrivi a chi non ha fatto niente» opens the new draft', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) =>
      String(input).endsWith('/follow-up') && init?.method === 'POST'
        ? json({ ...SENT.campaign, id: 'c2', source: 'list', follows_id: 'c1', status: 'draft' }, 201)
        : json(SENT),
    )
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Riscrivi a chi non ha fatto niente (2)' }))
    expect(await screen.findByText('modifica')).toBeInTheDocument()
    expect(fetch.mock.calls.some(([url]) => String(url).endsWith('/api/hub/campaigns/c1/follow-up'))).toBe(true)
  })

  it('offers no «Riscrivi» before the send or once everyone acted', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(DETAIL))
    mount()
    await screen.findByText('ada@studio.it')
    expect(screen.queryByRole('button', { name: /Riscrivi/ })).not.toBeInTheDocument()
  })
})

describe('a stopped send (REB-524)', () => {
  it('says «Invio fermo» with the reason in place of «Parte il…»', async () => {
    const stopped = {
      ...DETAIL,
      campaign: { ...DETAIL.campaign, status: 'sending', stalled_at: '2026-09-26T07:31:00Z', stalled_reason: 'Resend rifiuta l’invio: controlla la chiave e il dominio del mittente' },
    }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(stopped))
    mount()
    expect(await screen.findByRole('status')).toHaveTextContent(
      'Invio fermo: Resend rifiuta l’invio: controlla la chiave e il dominio del mittente, dal 26 settembre 2026 alle 09:31 (ora di Roma). Si riprova ogni minuto e riparte da solo appena è risolto.',
    )
    expect(screen.getByText('Invio fermo')).toBeInTheDocument()
    expect(screen.queryByText('In invio')).not.toBeInTheDocument()
    expect(screen.queryByText(/^Parte il/)).not.toBeInTheDocument()
  })
})

describe('deleting a draft (REB-524)', () => {
  const draft = { ...DETAIL, campaign: { ...DETAIL.campaign, status: 'draft', scheduled_for: null }, recipients: [] }

  it('deletes it after a second click and goes back to the list', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) =>
      init?.method === 'DELETE' ? new Response(null, { status: 204 }) : json(draft),
    )
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Elimina' }))
    expect(screen.getByText('Eliminare la bozza?')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Indietro' }))
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false)
    await userEvent.click(screen.getByRole('button', { name: 'Elimina' }))
    await userEvent.click(screen.getByRole('button', { name: 'Conferma' }))
    expect(await screen.findByText('elenco')).toBeInTheDocument()
    const call = fetch.mock.calls.find(([, init]) => init?.method === 'DELETE')!
    expect(String(call[0])).toMatch(/\/api\/hub\/campaigns\/c1$/)
  })

  it('says why when the server refuses, and stays', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) =>
      init?.method === 'DELETE' ? json({ detail: 'Si elimina solo una bozza.' }, 409) : json(draft),
    )
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Elimina' }))
    await userEvent.click(screen.getByRole('button', { name: 'Conferma' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Si elimina solo una bozza.')
  })

  it('is not offered once the campaign is scheduled or sent', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(DETAIL))
    mount()
    await screen.findByText('ada@studio.it')
    expect(screen.queryByRole('button', { name: 'Elimina' })).not.toBeInTheDocument()
  })
})

describe('«Riscrivi» and a bounced mail (REB-524)', () => {
  it('counts neither a bounced nor a complained row as waiting', async () => {
    const [ada, bob, cleo] = SENT.recipients
    const withBounces = {
      ...SENT,
      recipients: [
        ada,
        { ...bob!, bounced_at: '2026-09-26T07:33:00Z' },
        { ...cleo!, complaint_at: '2026-09-26T08:00:00Z' },
        { ...cleo!, id: 'r4', email: 'dora@studio.it', name: 'Dora' },
      ],
    }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(withBounces))
    mount()
    expect(await screen.findByRole('button', { name: 'Riscrivi a chi non ha fatto niente (1)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Non ha fatto niente (1)' })).toBeInTheDocument()
  })
})
