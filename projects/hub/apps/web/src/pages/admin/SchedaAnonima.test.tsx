import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SchedaAnonima } from './SchedaAnonima'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

// Written as escapes on purpose, as `bands.test.ts` does: an en dash looks like a hyphen.
const DASH = '\u2013'

/** `GET /api/hub/freelancers/{id}/card` for a CV that produced a card. */
const WRITTEN = {
  freelancer_id: 'f1',
  card: {
    ruolo: 'Backend developer',
    seniority: 'senior',
    anni: 9,
    competenze: ['Python', 'FastAPI', 'PostgreSQL'],
    settori: ['fintech', 'e-commerce'],
    lingue: ['italiano', 'inglese'],
    luogo: 'Torino',
    sintesi: 'Backend developer senior, nove anni fra fintech ed e-commerce.',
  },
  modalita: 'ibrido',
  // Core's band of the rate on file (450 € a day plus 40% is 630), read with the card.
  fascia: { min: 500, max: 650 },
  cv_sha256: 'a'.repeat(64),
  model: 'claude-opus-5',
  generated_at: '2026-09-25T10:00:00Z',
  error: null,
}

const NONE = {
  freelancer_id: 'f1',
  card: null,
  modalita: null,
  fascia: null,
  cv_sha256: null,
  model: null,
  generated_at: null,
  error: null,
}

/** A CV on file, unless the test says otherwise. */
function mount({ hasCv = true }: { hasCv?: boolean } = {}) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <SchedaAnonima freelancerId="f1" hasCv={hasCv} />
    </QueryClientProvider>,
  )
}

function section(): HTMLElement {
  return screen.getByRole('region', { name: 'Scheda anonima' })
}

afterEach(() => vi.restoreAllMocks())

describe('«Scheda anonima» on the talent page (REB-514, spec § 2.1, § 5.1)', () => {
  it('shows every field of the card, the mode, the band core read and when it was written', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, WRITTEN))
    mount()

    expect(await screen.findByText('Backend developer')).toBeInTheDocument()
    const card = section()
    expect(spy).toHaveBeenCalledWith('/api/hub/freelancers/f1/card', expect.anything())
    expect(within(card).getByText('Senior')).toBeInTheDocument()
    expect(within(card).getByText('9 anni di esperienza')).toBeInTheDocument()
    for (const skill of ['Python', 'FastAPI', 'PostgreSQL']) expect(within(card).getByText(skill)).toBeInTheDocument()
    expect(within(card).getByText('fintech, e-commerce')).toBeInTheDocument()
    expect(within(card).getByText('italiano, inglese')).toBeInTheDocument()
    expect(within(card).getByText('Torino')).toBeInTheDocument()
    expect(within(card).getByText(WRITTEN.card.sintesi)).toBeInTheDocument()
    expect(within(card).getByText('Ibrido')).toBeInTheDocument()
    // The band as core sent it, in the shared words. The query normalises the
    // non-breaking space before «€» to a plain one, so the text is matched that way.
    expect(within(card).getByText(`500${DASH}650 € al giorno`)).toBeInTheDocument()
    expect(within(card).getByText(/^25 set 2026.*claude-opus-5$/)).toBeInTheDocument()
    expect(within(card).getByRole('button', { name: 'Rigenera scheda' })).toBeInTheDocument()
  })

  it('says «Tariffa da definire» for a talent with no rate, and «—» for what the card leaves empty', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { ...WRITTEN, modalita: null, fascia: null, card: { ...WRITTEN.card, luogo: null, settori: [] } }),
    )
    mount()
    await screen.findByText('Backend developer')
    expect(within(section()).getByText('Tariffa da definire')).toBeInTheDocument()
    expect(within(section()).getAllByText('—')).toHaveLength(3)
  })

  it('writes a mode it has no word for as it came', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { ...WRITTEN, modalita: 'altrove' }))
    mount()
    await screen.findByText('Backend developer')
    expect(within(section()).getByText('altrove')).toBeInTheDocument()
  })

  it('shows the error sentence when a CV produced no card', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { ...NONE, error: 'Il CV non ha testo leggibile.' }))
    mount()
    expect(await screen.findByText('Il CV non ha testo leggibile.')).toBeInTheDocument()
    expect(within(section()).queryByText('Competenze')).toBeNull()
  })

  it('shows the last failure beside a card that is still there', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { ...WRITTEN, error: 'Claude non ha risposto: «Rigenera scheda» riprova.' }),
    )
    mount()
    await screen.findByText('Backend developer')
    expect(within(section()).getByText('Claude non ha risposto: «Rigenera scheda» riprova.')).toBeInTheDocument()
  })

  it('says there is no card yet, and without a CV offers nothing to regenerate', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, NONE))
    mount({ hasCv: false })
    expect(await screen.findByText('Nessuna scheda: manca il CV.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Rigenera scheda' })).toBeNull()
  })

  it('says there is no card yet for a CV not read so far', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, NONE))
    mount()
    expect(await screen.findByText('Nessuna scheda ancora.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Rigenera scheda' })).toBeInTheDocument()
  })

  it('writes the card again with «Rigenera scheda», saying so while it runs', async () => {
    let release!: (response: Response) => void
    const regenerated = new Promise<Response>((resolve) => {
      release = resolve
    })
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      if (init?.method === 'POST') return regenerated
      return answer(200, NONE)
    })
    mount()

    await userEvent.click(await screen.findByRole('button', { name: 'Rigenera scheda' }))
    expect(await screen.findByRole('button', { name: 'Rigenero…' })).toBeDisabled()
    expect(spy).toHaveBeenCalledWith('/api/hub/freelancers/f1/card', expect.objectContaining({ method: 'POST' }))

    release(answer(200, WRITTEN))
    expect(await screen.findByText('Backend developer')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Rigenera scheda' })).toBeEnabled()
  })

  it('shows the API’s sentence when the builder is off, and keeps the card on screen', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      if (init?.method === 'POST') return answer(503, { detail: 'Il team builder è spento.' })
      return answer(200, WRITTEN)
    })
    mount()

    await userEvent.click(await screen.findByRole('button', { name: 'Rigenera scheda' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Il team builder è spento.')
    expect(screen.getByText('Backend developer')).toBeInTheDocument()
  })

  it('says so when the card cannot be read', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(500, { detail: 'boom' }))
    mount()
    expect(await screen.findByText('Non riesco a leggere la scheda anonima.')).toBeInTheDocument()
  })
})
