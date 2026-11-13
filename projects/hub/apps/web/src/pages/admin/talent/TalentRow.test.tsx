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
