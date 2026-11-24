import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { COMPLETE, INCOMPLETE, LINKED_PAIR, answer, mount, noCard, openTab, person, resetMocks, routeFetch, settle } from '../list-test-kit'

afterEach(resetMocks)

/** `fetch` for the writes of this page: the reads (the card, the trail) answered as they are
 *  when nothing is wrong, the writes by `write`, and anything else with the freelancer. */
function serve(
  freelancer: Record<string, unknown> & { id: string },
  write: (url: string, init: RequestInit | undefined) => Response | undefined = () => undefined,
) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    const written = write(url, init)
    if (written) return written
    if (url === `/api/hub/freelancers/${freelancer.id}/audit`) return answer(200, [])
    if (url === `/api/hub/freelancers/${freelancer.id}/card`) return answer(200, noCard(freelancer.id))
    return answer(200, freelancer)
  })
}

/** The overflow menu's item: «Elimina», «Rimuovi CV». */
async function chooseFromMenu(name: string) {
  await userEvent.click(await screen.findByRole('button', { name: 'Altre azioni' }))
  await userEvent.click(await screen.findByRole('menuitem', { name }))
}

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
    await openTab('Contatti e dati')
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
    // The header's one line of facts: the work mode is there without opening a tab.
    expect(screen.getByText('Da remoto')).toBeInTheDocument()
    await openTab('Contatti e dati')
    expect(screen.getByText('compilata dalla persona')).toBeInTheDocument()
    expect(screen.getByText(/^3 · ultimo 11 set 2026/)).toBeInTheDocument()
  })
})

describe('the tabs (REB-1158)', () => {
  const PROFILE_READ = {
    freelancer_id: 'f2',
    state: 'draft',
    submitted_at: '2026-11-12T10:00:00Z',
    expires_at: '2027-11-12T10:00:00Z',
    expired: false,
    tool_version: '1.0.0',
    groundedness: 90,
    unsupported_anchors: [],
    unknown_technologies: 0,
    unknown_practices: 0,
    guard_hits: [],
    decided_at: null,
    decided_by_name: null,
    decision_note: null,
    profile: {
      window_from: '2026-04',
      window_to: '2026-05',
      products: 3,
      sessions: 44,
      instructions: 2701,
      summary: 'Lavora con l’AI in modo orchestrato.',
      domains: [],
      lenses: [],
      trajectory: null,
      stack: [],
      technologies: [],
      practices: [],
      projects: [],
      other_projects: 0,
    },
  }

  it('opens on the work profile when the person sent one, and on the anonymous card when not', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f2': COMPLETE,
      'GET /api/hub/freelancers/f2/audit': [],
      'GET /api/hub/freelancers/f2/card': noCard('f2'),
    })
    mount('/admin/freelance/f2', undefined, undefined, PROFILE_READ)
    await screen.findByRole('heading', { name: 'Grace Hopper' })
    expect(await screen.findByRole('tab', { name: /^Profilo di lavoro/, selected: true })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /^Scheda anonima/, selected: false })).toBeInTheDocument()
    // The state shows in the header and on the tab before the tab is opened.
    expect(within(screen.getByRole('banner')).getByText('Profilo da rivedere')).toBeInTheDocument()
  })

  it('opens on the anonymous card for a person with no profile', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f2': COMPLETE,
      'GET /api/hub/freelancers/f2/audit': [],
      'GET /api/hub/freelancers/f2/card': noCard('f2'),
    })
    mount('/admin/freelance/f2')
    await screen.findByRole('heading', { name: 'Grace Hopper' })
    expect(await screen.findByRole('tab', { name: /^Scheda anonima/, selected: true })).toBeInTheDocument()
    // «Profilo di lavoro» is still a tab: it says how the person sends one.
    await openTab('Profilo di lavoro')
    expect(await screen.findByText(/npx @letsrebase\/verify/)).toBeInTheDocument()
  })

  it('counts the flags on the anonymous card’s tab', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f2': COMPLETE,
      'GET /api/hub/freelancers/f2/audit': [],
      'GET /api/hub/freelancers/f2/card': {
        ...noCard('f2'),
        flags: [{ field: 'skills', verdict: 'unsupported', reason: 'Nessuna fonte.' }],
      },
    })
    mount('/admin/freelance/f2')
    const tab = await screen.findByRole('tab', { name: /^Scheda anonima/ })
    expect(tab).toHaveTextContent('Da verificare: 1')
  })

  it('opens with the way back to the list above the name, not at the foot of the page', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f2': COMPLETE,
      'GET /api/hub/freelancers/f2/audit': [],
      'GET /api/hub/freelancers/f2/card': noCard('f2'),
    })
    mount('/admin/freelance/f2')
    await screen.findByRole('heading', { name: 'Grace Hopper' })
    const back = within(screen.getByRole('banner')).getByRole('link', { name: 'Tutti i talenti' })
    expect(back).toHaveAttribute('href', '/admin/talent')
    expect(screen.getAllByRole('link', { name: 'Tutti i talenti' })).toHaveLength(1)
  })

  it('has no state editor: a card is in play until it is discarded', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f2': COMPLETE,
      'GET /api/hub/freelancers/f2/audit': [],
      'GET /api/hub/freelancers/f2/card': noCard('f2'),
    })
    mount('/admin/freelance/f2')
    await screen.findByRole('heading', { name: 'Grace Hopper' })
    expect(screen.queryByRole('group', { name: 'Stato' })).toBeNull()
    expect(within(screen.getByRole('banner')).queryByText('Nuovo')).toBeNull()
  })

  it('moves «Note» into «Storico» on a phone, where the rail is gone', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f2': COMPLETE,
      'GET /api/hub/freelancers/f2/audit': [],
      'GET /api/hub/freelancers/f2/card': noCard('f2'),
    })
    mount('/admin/freelance/f2', undefined, undefined, undefined, false)
    await screen.findByRole('heading', { name: 'Grace Hopper' })
    await screen.findByRole('tab', { name: /^Scheda anonima/ })
    expect(screen.queryByRole('complementary')).toBeNull()
    expect(screen.queryByRole('region', { name: 'Note' })).toBeNull()
    await openTab('Storico')
    expect(await screen.findByRole('region', { name: 'Note' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Registro delle modifiche' })).toBeInTheDocument()
  })

  it('keeps only the CV and the overflow menu in the header on a phone, the rest inside the menu', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f2': COMPLETE,
      'GET /api/hub/freelancers/f2/audit': [],
      'GET /api/hub/freelancers/f2/card': noCard('f2'),
    })
    mount('/admin/freelance/f2', undefined, undefined, undefined, false)
    const banner = await screen.findByRole('banner')
    expect(within(banner).getByRole('link', { name: /CV/ })).toBeInTheDocument()
    expect(within(banner).queryByRole('link', { name: 'Crea match' })).toBeNull()
    expect(within(banner).queryByRole('button', { name: 'Modifica scheda' })).toBeNull()
    await userEvent.click(within(banner).getByRole('button', { name: 'Altre azioni' }))
    expect(await screen.findByRole('menuitem', { name: 'Crea match' })).toHaveAttribute('href', '/admin/freelance/f2/match/new')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Modifica scheda' }))
    expect(await screen.findByLabelText('Posizione')).toBeInTheDocument()
  })

  it('gives each badge in the header an icon beside its text: «Verificato» and the profile’s state', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f2': { ...COMPLETE, vetted_at: '2026-09-25T09:00:00Z' },
      'GET /api/hub/freelancers/f2/audit': [],
      'GET /api/hub/freelancers/f2/card': noCard('f2'),
    })
    mount('/admin/freelance/f2', undefined, undefined, {
      freelancer_id: 'f2', state: 'rejected', submitted_at: '2026-11-12T10:00:00Z', expires_at: '2027-11-12T10:00:00Z', expired: false,
      tool_version: null, groundedness: 90, unsupported_anchors: [], unknown_technologies: 0, unknown_practices: 0, guard_hits: [],
      decided_at: null, decided_by_name: null, decision_note: 'No.',
      profile: { window_from: null, window_to: null, products: null, sessions: null, instructions: null, summary: null, domains: [], lenses: [], trajectory: null, stack: [], technologies: [], practices: [], projects: [], other_projects: 0 },
    })
    const banner = await screen.findByRole('banner')
    const rejected = await within(banner).findByText('Profilo rifiutato')
    expect(rejected.querySelector('svg')).not.toBeNull()
    expect(within(banner).getByText('Verificato').querySelector('svg')).not.toBeNull()
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
    // The header's line of facts reads the card's role and level.
    expect(within(screen.getByRole('banner')).getByText('Lead · 15 anni di esperienza')).toBeInTheDocument()
  })

  it('leaves the section off a deleted card, whose card the API no longer reads', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f2': { ...COMPLETE, deleted_at: '2026-09-23T10:00:00Z' },
      'GET /api/hub/freelancers/f2/audit': [],
    })
    mount('/admin/freelance/f2')
    await screen.findByRole('heading', { name: 'Grace Hopper' })
    expect(screen.queryByRole('region', { name: 'Scheda anonima' })).toBeNull()
    expect(screen.queryByRole('tab', { name: /^Scheda anonima/ })).toBeNull()
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
    serve(withComment, (url, init) => {
      if (url !== '/api/hub/freelancers/f1/override' || init?.method !== 'PATCH') return undefined
      const body = JSON.parse(init.body as string)
      expect(body.job_title).toBe('Staff engineer')
      // The override response's own `note` is always `[]` (only `get` fills it):
      // a merge that took it verbatim would wipe the existing thread from view.
      return answer(200, { ...withComment, job_title: 'Staff engineer', note: [] })
    })
    mount('/admin/freelance/f1')
    await screen.findByRole('heading', { name: 'Ada Lovelace' })
    await screen.findByText('Ha risposto alla call.')
    await userEvent.click(screen.getByRole('button', { name: 'Modifica scheda' }))
    const jobTitle = await screen.findByLabelText('Posizione')
    await userEvent.type(jobTitle, 'Staff engineer')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(within(screen.getByRole('banner')).getByText('Staff engineer')).toBeInTheDocument())
    expect(screen.getByText('Ha risposto alla call.')).toBeInTheDocument()
  })

  it('deletes the card from the overflow menu, hides the CV/edit actions, then restores it', async () => {
    const spy = serve(COMPLETE, (url, init) => {
      if (url === '/api/hub/freelancers/f2' && init?.method === 'DELETE') {
        return answer(200, { ...COMPLETE, deleted_at: '2026-09-23T10:00:00Z' })
      }
      if (url === '/api/hub/freelancers/f2/restore' && init?.method === 'POST') {
        return answer(200, { ...COMPLETE, deleted_at: null })
      }
      return undefined
    })
    mount('/admin/freelance/f2')
    await screen.findByRole('heading', { name: 'Grace Hopper' })
    expect(screen.getByRole('button', { name: 'Modifica scheda' })).toBeInTheDocument()

    await chooseFromMenu('Elimina')
    await screen.findByText(/Eliminata il/)
    expect(screen.queryByRole('button', { name: 'Modifica scheda' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Altre azioni' })).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: 'Ripristina' }))
    await waitFor(() => expect(screen.queryByText(/Eliminata il/)).toBeNull())
    expect(screen.getByRole('button', { name: 'Modifica scheda' })).toBeInTheDocument()
    expect(spy).toHaveBeenCalledWith('/api/hub/freelancers/f2', expect.objectContaining({ method: 'DELETE' }))
  })

  it('falls back to a tab that still exists when the card is deleted from a tab that goes with it', async () => {
    serve(COMPLETE, (url, init) =>
      url === '/api/hub/freelancers/f2' && init?.method === 'DELETE'
        ? answer(200, { ...COMPLETE, deleted_at: '2026-09-23T10:00:00Z' })
        : undefined,
    )
    mount('/admin/freelance/f2')
    await screen.findByRole('heading', { name: 'Grace Hopper' })
    await openTab('Scheda anonima')
    expect(await screen.findByRole('region', { name: 'Scheda anonima' })).toBeInTheDocument()

    await chooseFromMenu('Elimina')
    await screen.findByText(/Eliminata il/)
    // The card's tab is gone with the card: the page shows another one, not an empty body.
    const panel = await screen.findByRole('tabpanel')
    expect(panel).not.toBeEmptyDOMElement()
    expect(screen.getByRole('tab', { name: /^Contatti e dati/, selected: true })).toBeInTheDocument()
  })

  it('clears the CV from the overflow menu and drops its download link and menu item', async () => {
    serve(COMPLETE, (url, init) =>
      url === '/api/hub/freelancers/f2/cv' && init?.method === 'DELETE'
        ? answer(200, { ...COMPLETE, cv_filename: null, cv_size: null })
        : undefined,
    )
    mount('/admin/freelance/f2')
    await screen.findByRole('heading', { name: 'Grace Hopper' })
    await chooseFromMenu('Rimuovi CV')
    await waitFor(() => expect(screen.queryByRole('link', { name: /CV/ })).toBeNull())
    await userEvent.click(screen.getByRole('button', { name: 'Altre azioni' }))
    expect(await screen.findByRole('menuitem', { name: 'Elimina' })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: 'Rimuovi CV' })).toBeNull()
  })
})

describe('«Scarta» and «Riprendi» (REB-1164)', () => {
  it('discards the card from the overflow menu, keeps the notes thread, then takes it back', async () => {
    const withComment = {
      ...INCOMPLETE,
      note: [
        { id: 'c1', text: 'Ha risposto alla call.', author: 'Ivan', author_id: null, source: 'web', created_at: '2026-09-12T10:00:00Z', edited_at: null, edited_by: null, editable: true },
      ],
    }
    const sent: string[] = []
    serve(withComment, (url, init) => {
      if (url !== '/api/hub/freelancers/f1' || init?.method !== 'PATCH') return undefined
      const { status } = JSON.parse(init.body as string)
      sent.push(status)
      // The move response's own `note` is always `[]` (only `get` fills it, REB-356):
      // a replace that took it verbatim would wipe the existing thread from view.
      return answer(200, { ...withComment, status, note: [] })
    })
    mount('/admin/freelance/f1')
    await screen.findByRole('heading', { name: 'Ada Lovelace' })
    await screen.findByText('Ha risposto alla call.')

    await chooseFromMenu('Scarta')
    await within(screen.getByRole('banner')).findByText('Scartato')
    expect(screen.getByText('Ha risposto alla call.')).toBeInTheDocument()

    await chooseFromMenu('Riprendi')
    await waitFor(() => expect(within(screen.getByRole('banner')).queryByText('Scartato')).toBeNull())
    expect(sent).toEqual(['discarded', 'new'])
  })

  it('says so in the header when the discard is refused and keeps the card in play', async () => {
    serve(INCOMPLETE, (url, init) =>
      url === '/api/hub/freelancers/f1' && init?.method === 'PATCH'
        ? answer(409, { detail: 'Lo stato non può cambiare.' })
        : undefined,
    )
    mount('/admin/freelance/f1')
    await screen.findByRole('heading', { name: 'Ada Lovelace' })

    await chooseFromMenu('Scarta')

    expect(await screen.findByRole('alert')).toHaveTextContent('Lo stato non può cambiare.')
    expect(within(screen.getByRole('banner')).queryByText('Scartato')).toBeNull()
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
    await openTab('Contatti e dati')
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
    await openTab('Contatti e dati')
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
