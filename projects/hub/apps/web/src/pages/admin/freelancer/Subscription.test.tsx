import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { COMPLETE, INCOMPLETE, answer, mount, noCard, resetMocks, routeFetch } from '../list-test-kit'

afterEach(resetMocks)

describe('the enriched detail: sign-up, logins, downloads, Pigro space (REB-284)', () => {
  const ENRICHED = {
    ...COMPLETE,
    signup_utm: {
      utm_source: 'newsletter',
      utm_medium: 'email',
      utm_campaign: 'autunno-2026',
      utm_content: null,
      utm_term: null,
      utm_id: null,
    },
    last_logins: [
      { id: 'l1', logged_at: '2026-09-12T09:00:00Z' },
      { id: 'l2', logged_at: '2026-09-11T09:00:00Z' },
    ],
    last_download_guide: [{ id: 'd1', downloaded_at: '2026-09-10T09:00:00Z' }],
    pigro_slug: 'studio-grace',
  }


  it('shows the sign-up utm, the recent logins and downloads, and the Pigro slug, in order', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f2': ENRICHED,
      'GET /api/hub/freelancers/f2/audit': [],
      'GET /api/hub/freelancers/f2/card': noCard('f2'),
    })
    mount('/admin/freelance/f2')
    await screen.findByRole('heading', { name: 'Grace Hopper' })
    const headings = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
    expect(headings).toEqual([
      'Note',
      'Scheda anonima',
      'Iscrizione alla newsletter',
      'Ultimi accessi',
      'Download della guida',
      'Spazio PigroCRM',
      'Registro delle modifiche',
    ])
    expect(screen.getByText('newsletter')).toBeInTheDocument()
    expect(screen.getByText('email')).toBeInTheDocument()
    expect(screen.getByText('autunno-2026')).toBeInTheDocument()
    expect(screen.getByText('studio-grace')).toBeInTheDocument()
  })

  it('shows sensible empty values with none of the four sources, not a crash', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': {
        ...INCOMPLETE,
        signup_utm: null,
        last_logins: [],
        last_download_guide: [],
        pigro_slug: null,
      },
      'GET /api/hub/freelancers/f1/audit': [],
      'GET /api/hub/freelancers/f1/card': noCard('f1'),
    })
    mount('/admin/freelance/f1')
    await screen.findByRole('heading', { name: 'Ada Lovelace' })
    expect(screen.getByText('Nessuna iscrizione con questo indirizzo.')).toBeInTheDocument()
    expect(screen.getByText('Non è mai entrata.')).toBeInTheDocument()
    expect(screen.getByText('Non ha scaricato la guida.')).toBeInTheDocument()
    // No slug at all: the section does not render rather than showing an empty one.
    expect(screen.queryByText('Spazio PigroCRM')).toBeNull()
  })

  it('keeps the enriched sections after saving a state change from the plain PATCH response', async () => {
    // `PATCH /freelancers/{id}` answers a plain `FreelancerRead`: none of REB-284's
    // keys even exist on the body, since only `get`'s response model carries them.
    const plainCard: Record<string, unknown> = { ...ENRICHED, status: 'contacted' }
    delete plainCard.signup_utm
    delete plainCard.last_logins
    delete plainCard.last_download_guide
    delete plainCard.pigro_slug
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/freelancers/f2/audit') return answer(200, [])
      if (init?.method === 'PATCH') return answer(200, plainCard)
      return answer(200, ENRICHED)
    })
    mount('/admin/freelance/f2')
    await screen.findByRole('heading', { name: 'Grace Hopper' })
    expect(screen.getByText('studio-grace')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Contattato' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    const banner = await screen.findByRole('banner')
    await within(banner).findByText('Contattato')
    // The save must merge onto the cached detail, not replace it: the Pigro slug and
    // the other REB-284 sections the PATCH never answers stay on the page.
    expect(screen.getByText('studio-grace')).toBeInTheDocument()
    expect(screen.getByText('newsletter')).toBeInTheDocument()
  })
})
