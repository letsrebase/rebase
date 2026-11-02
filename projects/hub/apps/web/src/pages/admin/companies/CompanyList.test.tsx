import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { COMPANY_A, COMPANY_B, COMPANY_TEAM_BUILDER, answer, mount, resetMocks, settle } from '../list-test-kit'

afterEach(resetMocks)

describe('the search box debounces before it reaches the API and the URL (REB-286)', () => {
  it('waits for a pause in typing before searching Aziende', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { totale: 0, items: [], per_stato: {} }),
    )
    mount('/admin/companies')
    await screen.findByRole('heading', { name: 'Aziende' })
    spy.mockClear()

    await userEvent.type(screen.getByLabelText('Cerca'), 'rossi')
    await settle()

    const calls = spy.mock.calls.map((call) => String(call[0])).filter((url) => url.includes('/api/hub/companies'))
    expect(calls).toHaveLength(1)
    expect(calls[0]).toContain('q=rossi')
  })
})

describe('every filter and the search box live in the URL, both ways (REB-286)', () => {
  it('reflects a filter field into the address for Aziende', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { totale: 0, items: [], per_stato: {} }))
    const router = mount('/admin/companies')
    await screen.findByRole('heading', { name: 'Aziende' })

    await userEvent.type(screen.getByLabelText('Pagina di provenienza'), 'home')

    await waitFor(() => expect(router.state.location.search).toMatchObject({ origine: 'home' }))
  })

  it('reads a filter back out of a URL a link already carries, for Aziende', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { totale: 0, items: [], per_stato: {} }))
    mount('/admin/companies?origine=pigrocrm')
    await screen.findByRole('heading', { name: 'Aziende' })
    expect(screen.getByLabelText('Pagina di provenienza')).toHaveValue('pigrocrm')
  })
})

describe('infinite scroll walks the cursor, a page at a time (REB-286)', () => {
  it('shows a second page of Aziende after «Mostra altri»', async () => {
    const page1 = { totale: 2, items: [COMPANY_A], per_stato: { nuovo: 1 }, next_cursor: 'CURSOR1' }
    const page2 = { totale: 2, items: [COMPANY_B], per_stato: { nuovo: 1, contattato: 1 }, next_cursor: null }
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = new URL(String(input), 'http://test')
      return answer(200, url.searchParams.get('cursor') === 'CURSOR1' ? page2 : page1)
    })
    mount('/admin/companies')

    await screen.findByText('Rossi Studio')
    expect(screen.getByText('Mostrate 1 aziende, ce ne sono altre.')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Mostra altri' }))

    await screen.findByText('Bianchi Srl')
    expect(screen.getByText('Rossi Studio')).toBeInTheDocument()
  })
})

describe('two empty states, in Italian (REB-286)', () => {
  it('says the Aziende table itself is empty with no filter active', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { totale: 0, items: [], per_stato: {} }))
    mount('/admin/companies')
    expect(await screen.findByText('Nessuna richiesta qui.')).toBeInTheDocument()
  })

  it('names the filters when one narrows Aziende to nothing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { totale: 0, items: [], per_stato: {} }))
    mount('/admin/companies?origine=home')
    expect(await screen.findByText('Nessun risultato per questi filtri.')).toBeInTheDocument()
  })
})

describe('the Aziende list renders a request (REB-286, previously untested)', () => {
  it('lists a request with its budget and period, and the total count', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { totale: 1, items: [COMPANY_A], per_stato: { nuovo: 1 } }),
    )
    mount('/admin/companies')
    expect(await screen.findByText('Rossi Studio')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('1')
  })
})

describe('«da team builder» and the talent cloud on Aziende (REB-518)', () => {
  it('names the team builder on the row of a request that came from it, and on no other', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { totale: 2, items: [COMPANY_TEAM_BUILDER, COMPANY_B], per_stato: {} }),
    )
    mount('/admin/companies')
    const rossi = (await screen.findByText('Rossi Studio')).closest('tr')!
    expect(within(rossi).getByText('da team builder')).toBeInTheDocument()
    const bianchi = screen.getByText('Bianchi Srl').closest('tr')!
    expect(within(bianchi).queryByText('da team builder')).toBeNull()
  })
})
