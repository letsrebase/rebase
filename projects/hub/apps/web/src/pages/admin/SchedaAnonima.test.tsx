import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
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
    // The level editor's own select carries a hidden option of the same word.
    expect(within(card).getByText('Senior', { selector: 'span' })).toBeInTheDocument()
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

  describe('«Scarica CV anonimo»', () => {
    it('is absent without a CV', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, NONE))
      mount({ hasCv: false })
      await screen.findByText('Nessuna scheda: manca il CV.')
      expect(screen.queryByRole('button', { name: 'Scarica CV anonimo' })).toBeNull()
    })

    it('asks for the PDF, says so while it runs and saves it under the server’s filename', async () => {
      let release!: (response: Response) => void
      const pdf = new Promise<Response>((resolve) => {
        release = resolve
      })
      const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
        if (init?.method === 'POST') return pdf
        return answer(200, WRITTEN)
      })
      const create = vi.fn(() => 'blob:cv')
      const revoke = vi.fn()
      Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke })
      let saved: string | null = null
      vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
        saved = this.download
      })
      mount()

      await userEvent.click(await screen.findByRole('button', { name: 'Scarica CV anonimo' }))
      expect(await screen.findByRole('button', { name: 'Preparo il CV…' })).toBeDisabled()
      expect(spy).toHaveBeenCalledWith(
        '/api/hub/freelancers/f1/cv-anonimo',
        expect.objectContaining({ method: 'POST' }),
      )

      release(
        new Response('%PDF-1.7', {
          status: 200,
          headers: {
            'Content-Type': 'application/pdf',
            'Content-Disposition': 'attachment; filename="rebase-cv-backend-developer-12345678.pdf"',
          },
        }),
      )
      await waitFor(() => expect(saved).toBe('rebase-cv-backend-developer-12345678.pdf'))
      expect(create).toHaveBeenCalledTimes(1)
      await waitFor(() => expect(revoke).toHaveBeenCalledWith('blob:cv'))
      expect(screen.getByRole('button', { name: 'Scarica CV anonimo' })).toBeEnabled()
    })

    it('shows the API’s sentence when the CV cannot be prepared', async () => {
      vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
        if (init?.method === 'POST') return answer(422, { detail: 'Il CV contiene ancora il nome della persona.' })
        return answer(200, WRITTEN)
      })
      mount()
      await userEvent.click(await screen.findByRole('button', { name: 'Scarica CV anonimo' }))
      expect(await screen.findByRole('alert')).toHaveTextContent('Il CV contiene ancora il nome della persona.')
    })
  })

  it('says the years are not in the CV and the seniority is a guess, when the card dates nothing (REB-799)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { ...WRITTEN, card: { ...WRITTEN.card, seniority: 'mid', anni: null, seniority_incerta: true } }),
    )
    mount()
    await screen.findByText('Backend developer')
    expect(within(section()).getByText('Anni non indicati nel CV')).toBeInTheDocument()
    expect(within(section()).getByText('Da verificare')).toBeInTheDocument()
    expect(within(section()).queryByText('Corretta dall’admin')).toBeNull()
  })

  it('shows what the admin corrected, and takes the correction off with «Togli la correzione»', async () => {
    let call = 0
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      if (init?.method === 'PATCH') return answer(200, {})
      call += 1
      return answer(200, {
        ...WRITTEN,
        card: { ...WRITTEN.card, seniority: 'lead', anni: 14 },
        seniority_admin: call === 1 ? 'lead' : null,
        anni_admin: call === 1 ? 14 : null,
      })
    })
    mount()
    await screen.findByText('Backend developer')
    expect(within(section()).getAllByText('Corretta dall’admin')).toHaveLength(2)
    expect(within(section()).getByLabelText('Anni dell’admin')).toHaveValue(14)

    await userEvent.click(within(section()).getByRole('button', { name: 'Togli la correzione' }))

    const patch = spy.mock.calls.find(([, init]) => init?.method === 'PATCH')
    expect(patch?.[0]).toBe('/api/hub/freelancers/f1/override')
    expect(JSON.parse(String(patch?.[1]?.body))).toEqual({ seniority_admin: null, anni_admin: null })
    expect(await screen.findByText('Backend developer')).toBeInTheDocument()
  })

  it('saves the admin’s seniority and years through the override, and refuses years it cannot save', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      if (init?.method === 'PATCH') return answer(200, {})
      return answer(200, WRITTEN)
    })
    mount()
    await screen.findByText('Backend developer')
    const editor = section()
    expect(within(editor).queryByRole('button', { name: 'Togli la correzione' })).toBeNull()

    await userEvent.click(within(editor).getByRole('combobox', { name: 'Seniority dell’admin' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Junior' }))
    const years = within(editor).getByLabelText('Anni dell’admin')
    await userEvent.type(years, '2.5')
    expect(within(editor).getByText('Gli anni sono un numero intero da 0 a 60.')).toBeInTheDocument()
    expect(within(editor).getByRole('button', { name: 'Salva il livello' })).toBeDisabled()

    await userEvent.clear(years)
    await userEvent.type(years, '2')
    await userEvent.click(within(editor).getByRole('button', { name: 'Salva il livello' }))

    const patch = spy.mock.calls.find(([, init]) => init?.method === 'PATCH')
    expect(patch?.[0]).toBe('/api/hub/freelancers/f1/override')
    expect(JSON.parse(String(patch?.[1]?.body))).toEqual({ seniority_admin: 'junior', anni_admin: 2 })
  })

  it('says so when the card cannot be read', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(500, { detail: 'boom' }))
    mount()
    expect(await screen.findByText('Non riesco a leggere la scheda anonima.')).toBeInTheDocument()
  })

  it('shows the provenance, the model’s sources labelled «secondo il modello», conflicts and staleness', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, {
        ...WRITTEN,
        provenance: {
          prompt_version: 2,
          provider: 'anthropic',
          fonti: { ruolo: ['cv', 'github'], competenze: ['cv', 'link:2'], sintesi: ['hub'] },
          conflicts: [
            { kind: 'anni_diversi', anni_scheda: 9, anni_hub: 12 },
            { kind: 'anni_scartata', anni_scheda: 6, anni_hub: null },
          ],
          sources: [
            { key: 'github', kind: 'github', ref: 'ada', status: 'ok', fetched_at: '2026-09-25T10:00:00Z' },
            { key: 'link:2', kind: 'link', ref: 'https://ada.dev', status: 'non raggiungibile', fetched_at: '2026-09-25T10:00:00Z' },
          ],
          source_hashes: {},
          stale: 'source',
        },
      }),
    )
    mount()
    await screen.findByText('Backend developer')
    const card = section()
    expect(within(card).getByText('Fonti, secondo il modello')).toBeInTheDocument()
    expect(within(card).getByText('Ruolo: CV, GitHub')).toBeInTheDocument()
    expect(within(card).getByText('Competenze: CV, Link 2')).toBeInTheDocument()
    expect(within(card).getByText('Sintesi: Dati della hub')).toBeInTheDocument()
    expect(within(card).getByText(/Anthropic · Prompt v2/)).toBeInTheDocument()
    expect(within(card).getByText(/GitHub: ada, letta il/)).toBeInTheDocument()
    expect(within(card).getByText(/Link 2: https:\/\/ada.dev, letta il .*\(non raggiungibile\)/)).toBeInTheDocument()
    expect(
      within(card).getByText('Gli incarichi completati in hub fanno 12 anni, la scheda ne dichiara 9 (date del CV)'),
    ).toBeInTheDocument()
    expect(
      within(card).getByText('Anni scartati: 6, venivano solo da fonti senza date professionali'),
    ).toBeInTheDocument()
    expect(within(card).getByText(/Da aggiornare: le fonti/)).toBeInTheDocument()
  })

  it('says a card written before the sources were recorded, and that a changed CV rewrites itself', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, {
        ...WRITTEN,
        provenance: {
          prompt_version: 1,
          provider: null,
          fonti: {},
          conflicts: [],
          sources: [],
          source_hashes: {},
          stale: 'cv',
        },
      }),
    )
    mount()
    await screen.findByText('Backend developer')
    const card = section()
    expect(within(card).getByText('Scheda scritta prima della registrazione delle fonti')).toBeInTheDocument()
    expect(within(card).getByText(/Prompt v1/)).toBeInTheDocument()
    expect(within(card).getByText('Il CV è cambiato: la scheda si rigenera da sola')).toBeInTheDocument()
  })
})

describe('the CV’s data stays out of «Scheda anonima» (REB-855)', () => {
  it('has no «Scheda con i dati» section: the data lives in the profile card', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, {
        ...WRITTEN,
        persona: {
          nome: 'Ada',
          cognome: 'Lovelace',
          email: 'ada.lovelace@example.org',
          telefono: '+39 333 123 4567',
          citta: 'Bologna',
          linkedin_url: null,
          altri_link: [],
          aziende: ['Initech Labs'],
        },
        differenze: [{ campo: 'email', cv: 'ada.lovelace@example.org', profilo: 'ada@studio.it' }],
      }),
    )
    mount()
    await screen.findByText('Backend developer')
    expect(screen.queryByRole('region', { name: 'Scheda con i dati' })).toBeNull()
    const card = section()
    expect(within(card).getByText(WRITTEN.card.sintesi)).toBeInTheDocument()
    expect(within(card).queryByText('Bologna')).toBeNull()
    expect(within(card).queryByText('Initech Labs')).toBeNull()
    expect(within(card).queryByRole('alert')).toBeNull()
  })
})
