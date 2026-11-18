import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CARD_TALENT, COMPLETE, LEAD_TALENT, VETTED_TALENT, answer, cellUnder, mount, resetMocks } from '../list-test-kit'

afterEach(resetMocks)

describe('the talent row menu (REB-387)', () => {
  it('offers its actions on a card row and no menu on a lead', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { total: 2, items: [CARD_TALENT, LEAD_TALENT], by_status: {} }),
    )
    mount('/admin/talent')
    const bob = (await screen.findByText('bob@example.org')).closest('tr')!
    expect(within(bob).queryByRole('button', { name: /Azioni per/ })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Azioni per Ada Lovelace' }))
    expect(await screen.findByRole('menuitem', { name: 'Crea match' })).toBeInTheDocument()
  })

  it('offers «Crea match» first in a card row menu', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { total: 1, items: [CARD_TALENT], by_status: {} }))
    mount('/admin/talent')
    await userEvent.click(await screen.findByRole('button', { name: 'Azioni per Ada Lovelace' }))
    const items = await screen.findAllByRole('menuitem')
    expect(items.map((item) => item.textContent)).toEqual([
      'Crea match',
      'Segna come verificato',
    ])
    expect(items[0]!.getAttribute('href')).toMatch(/\/admin\/freelance\/f1\/match\/new$/)
  })
})

describe('«Verificato» on Talenti and on the talent (REB-518)', () => {
  it('shows the pill on a vetted row only', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { total: 2, items: [VETTED_TALENT, LEAD_TALENT], by_status: {} }),
    )
    mount('/admin/talent')
    const ada = (await screen.findByText('ada@studio.it')).closest('tr')!
    expect(within(cellUnder(ada, 'Stato')).getByText('Verificato')).toBeInTheDocument()
    const bob = screen.getByText('bob@example.org').closest('tr')!
    expect(within(bob).queryByText('Verificato')).toBeNull()
  })

  it('marks a row verified from its menu and reads the list again', async () => {
    let vetted = false
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/freelancers/f1/vetted' && init?.method === 'POST') {
        vetted = JSON.parse(init.body as string).vetted
        return answer(200, { ...COMPLETE, id: 'f1', vetted_at: vetted ? '2026-09-26T10:00:00Z' : null })
      }
      const row = vetted ? VETTED_TALENT : CARD_TALENT
      return answer(200, { total: 1, items: [row], by_status: {} })
    })
    mount('/admin/talent')
    await userEvent.click(await screen.findByRole('button', { name: 'Azioni per Ada Lovelace' }))
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Segna come verificato' }))
    const ada = (await screen.findByText('ada@studio.it')).closest('tr')!
    await within(ada).findByText('Verificato')
    expect(spy).toHaveBeenCalledWith(
      '/api/hub/freelancers/f1/vetted',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ vetted: true }) }),
    )

    await userEvent.click(screen.getByRole('button', { name: 'Azioni per Ada Lovelace' }))
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Togli la verifica' }))
    await waitFor(() => expect(within(ada).queryByText('Verificato')).toBeNull())
    expect(spy).toHaveBeenCalledWith(
      '/api/hub/freelancers/f1/vetted',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ vetted: false }) }),
    )
  })

  it('says so in the row when the verification cannot be saved', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      if (String(input) === '/api/hub/freelancers/f1/vetted' && init?.method === 'POST') {
        throw new TypeError('Failed to fetch')
      }
      return answer(200, { total: 1, items: [CARD_TALENT], by_status: {} })
    })
    mount('/admin/talent')
    await userEvent.click(await screen.findByRole('button', { name: 'Azioni per Ada Lovelace' }))
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Segna come verificato' }))
    const ada = screen.getByText('ada@studio.it').closest('tr')!
    expect(await within(ada).findByRole('alert')).toHaveTextContent('Non riesco a salvare la verifica.')
    expect(within(ada).queryByText('Verificato')).toBeNull()
  })
})

describe('«Da rivedere» on Talenti (REB-1126)', () => {
  it('shows the pill on a row whose work profile waits for an admin, and not on an approved one', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, {
        total: 2,
        items: [
          { ...CARD_TALENT, work_profile_state: 'draft' },
          { ...VETTED_TALENT, id: 'f9', email: 'grace@studio.it', work_profile_state: 'approved' },
        ],
        by_status: {},
      }),
    )
    mount('/admin/talent')
    const ada = (await screen.findByText('ada@studio.it')).closest('tr')!
    expect(within(cellUnder(ada, 'Stato')).getByText('Da rivedere')).toBeInTheDocument()
    const grace = screen.getByText('grace@studio.it').closest('tr')!
    expect(within(grace).queryByText('Da rivedere')).toBeNull()
  })

  it('asks the API for the review state the filter names', async () => {
    const spy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(answer(200, { total: 0, items: [], by_status: {} }))
    mount('/admin/talent?work_profile=draft')
    await waitFor(() =>
      expect(spy.mock.calls.some(([url]) => String(url).includes('work_profile=draft'))).toBe(true),
    )
    expect(await screen.findByLabelText('Profilo di lavoro')).toBeInTheDocument()
  })
})

describe('the usage bands on Talenti (REB-1130)', () => {
  const PYTHON = { name: 'Python', band: 'continuous', sessions: 40, products: 6, months: 12, first_month: '2025-07', last_month: '2026-06' } as const
  const ZOD = { name: 'Zod', band: 'recurring', sessions: 9, products: 2, months: 4, first_month: '2026-01', last_month: '2026-05' } as const
  const GO = { name: 'Go', band: 'occasional', sessions: 3, products: 1, months: 1, first_month: '2026-04', last_month: '2026-04' } as const

  it('shows an approved profile’s technologies in the skills cell, filled by band, with the legend once', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, {
        total: 2,
        items: [
          { ...VETTED_TALENT, skills: ['Python'], work_profile_state: 'approved', technologies: [GO, PYTHON, ZOD] },
          { ...CARD_TALENT, id: 'f9', email: 'grace@studio.it', work_profile_state: 'draft', technologies: [] },
        ],
        by_status: {},
      }),
    )
    mount('/admin/talent')
    const ada = (await screen.findByText('ada@studio.it')).closest('tr')!
    const cell = cellUnder(ada, 'Competenze')
    const badges = within(cell).getAllByRole('listitem').map((item) => item.querySelector('[data-slot="badge"]') as HTMLElement)
    expect(badges.map((b) => [b.firstChild?.textContent, b.getAttribute('data-fill')])).toEqual([
      ['Python', 'full'],
      ['Zod', 'two-thirds'],
      ['Go', 'third'],
    ])
    // The band and the counts are on hover through the title, and in the accessible name.
    expect(badges[1]).toHaveAttribute('title', 'Presenza ricorrente: 9 sessioni in 2 prodotti, 4 mesi, da gennaio 2026 a maggio 2026')
    expect(screen.getAllByText(/Il riempimento indica quanto spesso/)).toHaveLength(1)
    // A draft’s row, whose list the API leaves empty, shows none.
    const grace = screen.getByText('grace@studio.it').closest('tr')!
    expect(within(grace).queryByRole('list', { name: 'Tecnologie' })).toBeNull()
  })

  it('says «+N» for the technologies a row does not have room for', async () => {
    const many = ['A', 'B', 'C', 'D', 'E', 'F'].map((name) => ({ ...ZOD, name }))
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { total: 1, items: [{ ...VETTED_TALENT, work_profile_state: 'approved', technologies: many }], by_status: {} }),
    )
    mount('/admin/talent')
    const ada = (await screen.findByText('ada@studio.it')).closest('tr')!
    expect(within(ada).getAllByRole('listitem')).toHaveLength(5)
    expect(within(ada).getByText('+2')).toBeInTheDocument()
  })

  it('adds no legend and no list when no row has bands', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { total: 2, items: [CARD_TALENT, LEAD_TALENT], by_status: {} }),
    )
    mount('/admin/talent')
    await screen.findByText('ada@studio.it')
    expect(screen.queryByText(/Il riempimento indica quanto spesso/)).toBeNull()
    expect(screen.queryByRole('list', { name: 'Tecnologie' })).toBeNull()
  })
})
