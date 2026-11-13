import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { COMPLETE, INCOMPLETE, LINKED_PAIR, answer, mount, noCard, person, resetMocks, routeFetch, settle } from '../list-test-kit'

afterEach(resetMocks)

describe('the freelancer detail', () => {
  it('shows an incomplete card without a CV link and says the admin wrote it', async () => {
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': INCOMPLETE,
      'GET /api/hub/freelancers/f1/audit': [],
      'GET /api/hub/freelancers/f1/card': noCard('f1'),
    })
    mount('/admin/freelance/f1')
    await screen.findByRole('heading', { name: 'Ada Lovelace' })
    expect(spy).toHaveBeenCalledWith('/api/hub/freelancers/f1', expect.anything())
    expect(screen.queryByRole('link', { name: /CV/ })).toBeNull()
    expect(screen.getByText('Da completare')).toBeInTheDocument()
    expect(screen.getByText('scritta dall’admin, da completare')).toBeInTheDocument()
    expect(screen.getByText('Mai entrato')).toBeInTheDocument()
    // The three answers the person has not given yet read as dashes, not as a crash.
    const rows = screen.getAllByRole('definition')
    expect(rows.filter((row) => row.textContent === '—').length).toBeGreaterThanOrEqual(3)
  })

  it('shows a complete card with its CV and says the person filled it in', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f2': COMPLETE,
      'GET /api/hub/freelancers/f2/audit': [],
      'GET /api/hub/freelancers/f2/card': noCard('f2'),
    })
    mount('/admin/freelance/f2')
    await screen.findByRole('heading', { name: 'Grace Hopper' })
    expect(screen.getByRole('link', { name: /CV/ })).toHaveAttribute('href', '/api/hub/freelancers/f2/cv')
    expect(screen.queryByText('Da completare')).toBeNull()
    expect(screen.getByText('compilata dalla persona')).toBeInTheDocument()
    expect(screen.getByText('Da remoto')).toBeInTheDocument()
    expect(screen.getByText(/^3 · ultimo 11 set 2026/)).toBeInTheDocument()
  })
})

describe('the anonymous card on the freelancer detail (REB-514)', () => {
  it('reads the card of a live talent with a CV, and offers to write it again', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f2': COMPLETE,
      'GET /api/hub/freelancers/f2/audit': [],
      'GET /api/hub/freelancers/f2/card': {
        ...noCard('f2'),
        card: {
          role: 'CTO',
          seniority: 'lead',
          experience_years: 15,
          skills: ['Architettura'],
          industries: [],
          languages: ['italiano'],
          location: null,
          profile_summary: 'Guida team di prodotto da quindici anni.',
        },
        mode: 'remote',
        generated_at: '2026-09-25T10:00:00Z',
        model: 'claude-opus-5',
      },
    })
    mount('/admin/freelance/f2')
    const section = await screen.findByRole('region', { name: 'Scheda anonima' })
    expect(await within(section).findByText('Guida team di prodotto da quindici anni.')).toBeInTheDocument()
    expect(within(section).getByRole('button', { name: 'Rigenera scheda' })).toBeInTheDocument()
  })

  it('leaves the section off a deleted card, whose card the API no longer reads', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f2': { ...COMPLETE, deleted_at: '2026-09-23T10:00:00Z' },
      'GET /api/hub/freelancers/f2/audit': [],
    })
    mount('/admin/freelance/f2')
    await screen.findByRole('heading', { name: 'Grace Hopper' })
    expect(screen.queryByRole('region', { name: 'Scheda anonima' })).toBeNull()
  })
})

describe('REB-355: override, delete/restore and the audit trail on the freelancer detail', () => {
  it('overrides a field through "Modifica scheda" and shows it, keeping the existing notes thread', async () => {
    const withComment = {
      ...INCOMPLETE,
      note: [
        { id: 'c1', text: 'Ha risposto alla call.', author: 'Ivan', author_id: null, source: 'web', created_at: '2026-09-12T10:00:00Z', edited_at: null, edited_by: null, editable: true },
      ],
    }
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/freelancers/f1/audit') return answer(200, [])
      if (url === '/api/hub/freelancers/f1/override' && init?.method === 'PATCH') {
        const body = JSON.parse(init.body as string)
        expect(body.job_title).toBe('Staff engineer')
        // The override response's own `note` is always `[]` (only `get` fills it):
        // a merge that took it verbatim would wipe the existing thread from view.
        return answer(200, { ...withComment, job_title: 'Staff engineer', note: [] })
      }
      return answer(200, withComment)
    })
    mount('/admin/freelance/f1')
    await screen.findByRole('heading', { name: 'Ada Lovelace' })
    await screen.findByText('Ha risposto alla call.')
    await userEvent.click(screen.getByRole('button', { name: 'Modifica scheda' }))
    const jobTitle = await screen.findByLabelText('Posizione')
    await userEvent.type(jobTitle, 'Staff engineer')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(screen.getByText('Staff engineer')).toBeInTheDocument())
    expect(screen.getByText('Ha risposto alla call.')).toBeInTheDocument()
  })

  it('deletes the card, hides the CV/edit actions, then restores it', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/freelancers/f2/audit') return answer(200, [])
      if (url === '/api/hub/freelancers/f2' && init?.method === 'DELETE') {
        return answer(200, { ...COMPLETE, deleted_at: '2026-09-23T10:00:00Z' })
      }
      if (url === '/api/hub/freelancers/f2/restore' && init?.method === 'POST') {
        return answer(200, { ...COMPLETE, deleted_at: null })
      }
      return answer(200, COMPLETE)
    })
    mount('/admin/freelance/f2')
    await screen.findByRole('heading', { name: 'Grace Hopper' })
    expect(screen.getByRole('button', { name: 'Modifica scheda' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Elimina' }))
    await screen.findByText(/Eliminata il/)
    expect(screen.queryByRole('button', { name: 'Modifica scheda' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Rimuovi CV' })).toBeNull()
    // `set_status` 404s on a deleted row (`FreelancerService._require`'s own default):
    // the note/state editor must not offer an action the backend will refuse.
    expect(screen.queryByRole('button', { name: 'Contattato' })).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: 'Ripristina' }))
    await waitFor(() => expect(screen.queryByText(/Eliminata il/)).toBeNull())
    expect(screen.getByRole('button', { name: 'Modifica scheda' })).toBeInTheDocument()
    expect(spy).toHaveBeenCalledWith('/api/hub/freelancers/f2', expect.objectContaining({ method: 'DELETE' }))
  })

  it('clears the CV and drops its download link and button', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/freelancers/f2/audit') return answer(200, [])
      if (url === '/api/hub/freelancers/f2/cv' && init?.method === 'DELETE') {
        return answer(200, { ...COMPLETE, cv_filename: null, cv_size: null })
      }
      return answer(200, COMPLETE)
    })
    mount('/admin/freelance/f2')
    await screen.findByRole('heading', { name: 'Grace Hopper' })
    await userEvent.click(screen.getByRole('button', { name: 'Rimuovi CV' }))
    await waitFor(() => expect(screen.queryByRole('link', { name: /CV/ })).toBeNull())
    expect(screen.queryByRole('button', { name: 'Rimuovi CV' })).toBeNull()
  })
})

describe('REB-356: the state-change mutations keep the notes thread visible', () => {
  it('keeps the existing notes thread after a state change on the freelancer detail', async () => {
    const withComment = {
      ...INCOMPLETE,
      note: [
        { id: 'c1', text: 'Ha risposto alla call.', author: 'Ivan', author_id: null, source: 'web', created_at: '2026-09-12T10:00:00Z', edited_at: null, edited_by: null, editable: true },
      ],
    }
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/freelancers/f1/audit') return answer(200, [])
      if (url === '/api/hub/freelancers/f1' && init?.method === 'PATCH') {
        const body = JSON.parse(init.body as string)
        expect(body.status).toBe('contacted')
        // The move response's own `note` is always `[]` (only `get` fills it):
        // a replace that took it verbatim would wipe the existing thread from view.
        return answer(200, { ...withComment, status: 'contacted', note: [] })
      }
      return answer(200, withComment)
    })
    mount('/admin/freelance/f1')
    await screen.findByRole('heading', { name: 'Ada Lovelace' })
    await screen.findByText('Ha risposto alla call.')

    await userEvent.click(screen.getByRole('button', { name: 'Contattato' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    const banner = await screen.findByRole('banner')
    await within(banner).findByText('Contattato')
    expect(screen.getByText('Ha risposto alla call.')).toBeInTheDocument()
  })
})

describe('a state change that is refused', () => {
  it('says so under the state editor and keeps the old state on the freelancer detail', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/freelancers/f1/audit') return answer(200, [])
      if (url === '/api/hub/freelancers/f1' && init?.method === 'PATCH') {
        return answer(409, { detail: 'Lo stato non può cambiare.' })
      }
      return answer(200, INCOMPLETE)
    })
    mount('/admin/freelance/f1')
    await screen.findByRole('heading', { name: 'Ada Lovelace' })

    await userEvent.click(screen.getByRole('button', { name: 'Contattato' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Lo stato non può cambiare.')
    expect(within(screen.getByRole('banner')).getByText('Nuovo')).toBeInTheDocument()
  })
})

describe('the card no longer links to a matches page (REB-1108)', () => {
  it('has no «Match e contratti» link in its header', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f2': COMPLETE,
      'GET /api/hub/freelancers/f2/audit': [],
      'GET /api/hub/freelancers/f2/card': noCard('f2'),
    })
    mount('/admin/freelance/f2')
    await screen.findByRole('banner')
    expect(screen.queryByRole('link', { name: 'Match e contratti' })).toBeNull()
  })
})

describe('«Verificato» on Talenti and on the talent (REB-518)', () => {
  it('says on the talent\'s page whether and since when it is verified', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f2': { ...COMPLETE, vetted_at: '2026-09-25T09:00:00Z' },
      'GET /api/hub/freelancers/f2/audit': [],
      'GET /api/hub/freelancers/f2/card': noCard('f2'),
    })
    mount('/admin/freelance/f2')
    const banner = await screen.findByRole('banner')
    expect(within(banner).getByText('Verificato')).toBeInTheDocument()
    expect(screen.getByText('Verificato il 25 set 2026')).toBeInTheDocument()
  })

  it('says a talent nobody verified is not', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f2': { ...COMPLETE, vetted_at: null },
      'GET /api/hub/freelancers/f2/audit': [],
      'GET /api/hub/freelancers/f2/card': noCard('f2'),
    })
    mount('/admin/freelance/f2')
    const banner = await screen.findByRole('banner')
    expect(within(banner).queryByText('Verificato')).toBeNull()
    expect(screen.getByText('Non verificato')).toBeInTheDocument()
  })
})

describe('«Possibile duplicato» and the way into «Duplicati» (REB-800)', () => {
  it('flags a card\'s page with the other card of its pair', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': INCOMPLETE,
      'GET /api/hub/freelancers/f1/audit': [],
      'GET /api/hub/freelancers/f1/card': noCard('f1'),
    })
    mount('/admin/freelance/f1', { mergeable: [LINKED_PAIR], to_review: [], signups: 0 })
    const banner = await screen.findByRole('banner')
    expect(await within(banner).findByRole('link', { name: 'Possibile duplicato' })).toHaveAttribute(
      'href',
      '/admin/freelance/f2',
    )
  })

  it('shows nothing on the page of a card that is in no pair', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': INCOMPLETE,
      'GET /api/hub/freelancers/f1/audit': [],
      'GET /api/hub/freelancers/f1/card': noCard('f1'),
    })
    const elsewhere = { ...LINKED_PAIR, survivor: person('f8', 'Linus', 'Torvalds'), absorbed: person('f9', 'Dennis', 'Ritchie') }
    mount('/admin/freelance/f1', { mergeable: [elsewhere], to_review: [], signups: 0 })
    await screen.findByRole('banner')
    await settle()
    expect(screen.queryByText('Possibile duplicato')).toBeNull()
  })
})
