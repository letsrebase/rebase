import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { COMPLETE, INCOMPLETE, answer, mount, noCard, openTab, resetMocks, routeFetch } from '../list-test-kit'

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

  it('shows the sign-up utm and the Pigro slug under «Contatti e dati», the logins and downloads under «Storico»', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f2': ENRICHED,
      'GET /api/hub/freelancers/f2/audit': [],
      'GET /api/hub/freelancers/f2/card': noCard('f2'),
    })
    mount('/admin/freelance/f2')
    await screen.findByRole('heading', { name: 'Grace Hopper' })
    await openTab('Contatti e dati')
    expect(screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)).toEqual([
      'Iscrizione alla newsletter',
      'Spazio PigroCRM',
      'Note',
    ])
    expect(screen.getByText('newsletter')).toBeInTheDocument()
    expect(screen.getByText('email')).toBeInTheDocument()
    expect(screen.getByText('autunno-2026')).toBeInTheDocument()
    expect(screen.getByText('studio-grace')).toBeInTheDocument()

    await openTab('Storico')
    expect(screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)).toEqual([
      'Ultimi accessi',
      'Download della guida',
      'Registro delle modifiche',
      'Note',
    ])
    expect(screen.getAllByRole('listitem').filter((item) => /\d{4}/.test(item.textContent ?? '')).length).toBeGreaterThanOrEqual(3)
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
    await openTab('Contatti e dati')
    expect(screen.getByText('Nessuna iscrizione con questo indirizzo.')).toBeInTheDocument()
    // No slug at all: the section does not render rather than showing an empty one.
    expect(screen.queryByText('Spazio PigroCRM')).toBeNull()
    await openTab('Storico')
    expect(screen.getByText('Non è mai entrata.')).toBeInTheDocument()
    expect(screen.getByText('Non ha scaricato la guida.')).toBeInTheDocument()
  })

  it('keeps the enriched sections after a discard from the plain PATCH response', async () => {
    // `PATCH /freelancers/{id}` answers a plain `FreelancerRead`: none of REB-284's
    // keys even exist on the body, since only `get`'s response model carries them.
    const plainCard: Record<string, unknown> = { ...ENRICHED, status: 'discarded' }
    delete plainCard.signup_utm
    delete plainCard.last_logins
    delete plainCard.last_download_guide
    delete plainCard.pigro_slug
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/freelancers/f2/audit') return answer(200, [])
      if (url === '/api/hub/freelancers/f2/card') return answer(200, noCard('f2'))
      if (init?.method === 'PATCH') return answer(200, plainCard)
      return answer(200, ENRICHED)
    })
    mount('/admin/freelance/f2')
    await screen.findByRole('heading', { name: 'Grace Hopper' })

    await userEvent.click(screen.getByRole('button', { name: 'Altre azioni' }))
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Scarta' }))
    const banner = await screen.findByRole('banner')
    await within(banner).findByText('Scartato')
    // The save must merge onto the cached detail, not replace it: the Pigro slug and
    // the other REB-284 sections the PATCH never answers stay on the page.
    await openTab('Contatti e dati')
    expect(screen.getByText('studio-grace')).toBeInTheDocument()
    expect(screen.getByText('newsletter')).toBeInTheDocument()
  })
})
