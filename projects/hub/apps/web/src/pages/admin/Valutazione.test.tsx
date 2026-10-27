import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type * as Router from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdminValutazione, ProfileEditor } from './Valutazione'

// The page links to the editor; a router-less render only needs the links to be inert.
vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual<typeof Router>('@tanstack/react-router')
  return {
    ...actual,
    Link: ({ children, to }: { children: ReactNode; to: string }) => <a href={to}>{children}</a>,
    useParams: () => ({ id: 'f1' }),
  }
})

const CARD = {
  ruolo: 'Product designer',
  seniority: 'senior',
  anni: 6,
  competenze: ['Figma', 'Design system'],
  settori: ['Fintech'],
  lingue: ['italiano'],
  luogo: 'Milano',
  sintesi: 'Disegna prodotti digitali da sei anni.',
}
const profile = (extra = {}) => ({
  freelancer_id: 'f1',
  nome: 'Giulia',
  cognome: 'Verdi',
  label: 'designer',
  status: 'bozza',
  expected: CARD,
  corrected_by_nome: null,
  created_at: '2026-10-27T08:00:00Z',
  updated_at: '2026-10-27T08:00:00Z',
  ...extra,
})
const run = {
  id: 'r1',
  task: 'card',
  provider: 'anthropic',
  model: 'claude-opus-5',
  effort: 'medium',
  prompt_version: 3,
  created_at: '2026-10-27T09:00:00Z',
  created_by_nome: 'Ivan',
  summary: {},
  results: [
    {
      freelancer_id: 'f1',
      label: 'designer',
      anni_ok: true,
      seniority_ok: false,
      guard_clean: true,
      failure: null,
      latency_ms: 812,
      cost_eur: '0.012',
      card: CARD,
      ratings: null,
    },
  ],
}

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function mount(ui: ReactNode, data: unknown, handlers: Record<string, (body: unknown) => Response> = {}) {
  const calls: { path: string; method: string; body: unknown }[] = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const path = new URL(String(input), 'http://localhost').pathname
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ path, method: init?.method ?? 'GET', body })
    if (handlers[path]) return handlers[path](body)
    if (path === '/api/hub/admin/card-eval') return answer(200, data)
    return answer(404, { detail: 'not found' })
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
  return calls
}

afterEach(() => vi.restoreAllMocks())

describe('the Valutazione pages (REB-836)', () => {
  it('shows the bozza badge until the profile is accepted', async () => {
    mount(<AdminValutazione />, {
      profiles: [profile(), profile({ freelancer_id: 'f2', nome: 'Marco', status: 'accettata' })],
      runs: [],
    })
    const rows = await screen.findAllByRole('row')
    expect(within(rows[1]!).getByText('Bozza')).toBeInTheDocument()
    expect(within(rows[2]!).getByText('Accettata')).toBeInTheDocument()
    expect(within(rows[2]!).queryByText('Bozza')).toBeNull()
  })

  it('sends the card, seniority and years when saving a correction', async () => {
    const calls = mount(<ProfileEditor id="f1" />, { profiles: [profile()], runs: [] }, {
      '/api/hub/admin/card-eval/f1': () => answer(200, profile()),
    })
    const user = userEvent.setup()
    const anni = await screen.findByLabelText('Anni')
    await user.clear(anni)
    await user.type(anni, '8')
    await user.click(screen.getByRole('button', { name: 'Salva la correzione' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    const put = calls.find((c) => c.method === 'PUT')!
    expect(put.path).toBe('/api/hub/admin/card-eval/f1')
    expect(put.body).toMatchObject({
      ruolo: 'Product designer',
      seniority: 'senior',
      anni: 8,
      competenze: ['Figma', 'Design system'],
      luogo: 'Milano',
    })
  })

  it('cannot accept a profile without an expected card', async () => {
    mount(<ProfileEditor id="f1" />, { profiles: [profile({ expected: null })], runs: [] })
    expect(await screen.findByRole('button', { name: 'Accetta la scheda' })).toBeDisabled()
  })

  it('rates with three-point buttons and sends the ratings', async () => {
    const calls = mount(<AdminValutazione />, { profiles: [profile()], runs: [run] }, {
      '/api/hub/admin/card-eval/runs/r1/ratings': () => answer(200, run),
    })
    const user = userEvent.setup()
    const group = await screen.findByRole('group', { name: 'Ruolo di designer' })
    expect(within(group).getAllByRole('button')).toHaveLength(3)
    await user.click(within(group).getByRole('button', { name: 'Sì' }))
    expect(within(group).getByRole('button', { name: 'Sì' })).toHaveAttribute('aria-pressed', 'true')
    await user.click(within(screen.getByRole('group', { name: 'Sintesi di designer' })).getByRole('button', { name: 'No' }))
    await user.click(screen.getByRole('button', { name: 'Salva i giudizi' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true))
    expect(calls.find((c) => c.method === 'POST')!.body).toEqual({
      ratings: { f1: { ruolo: 3, sintesi: 1 } },
    })
  })
})
