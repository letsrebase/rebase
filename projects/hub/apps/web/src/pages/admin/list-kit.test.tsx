import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { answer, mount, resetMocks } from './list-test-kit'

afterEach(resetMocks)

describe('the euro filters read Italian thousands, whatever the browser’s locale (REB-485)', () => {
  /** Every URL the page asked the list endpoint for, in order. */
  function listCalls(spy: { mock: { calls: unknown[][] } }, endpoint: string): string[] {
    return spy.mock.calls.map((call) => String(call[0])).filter((url) => url.startsWith(endpoint))
  }

  const TALENTI = { path: '/admin/talent', heading: 'Talenti', endpoint: '/api/hub/talent' }
  const AZIENDE = { path: '/admin/companies', heading: 'Aziende', endpoint: '/api/hub/companies' }

  it.each([
    { ...TALENTI, label: 'Tariffa min (€/giorno)', param: 'tariffa_min' },
    { ...TALENTI, label: 'Tariffa max (€/giorno)', param: 'tariffa_max' },
    { ...AZIENDE, label: 'Budget min (€/giorno)', param: 'budget_min' },
    { ...AZIENDE, label: 'Budget max (€/giorno)', param: 'budget_max' },
  ])('«1.500» in «$label» asks for $param=1500.00', async ({ path, heading, label, endpoint, param }) => {
    const spy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async () => answer(200, { totale: 0, items: [], per_stato: {} }))
    mount(path)
    await screen.findByRole('heading', { name: heading })

    await userEvent.type(screen.getByLabelText(label), '1.500')

    await waitFor(() => expect(listCalls(spy, endpoint).at(-1)).toContain(`${param}=1500.00`))
    expect(screen.getByLabelText(label)).toHaveValue('1.500')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it.each([
    { ...TALENTI, label: 'Tariffa min (€/giorno)', param: 'tariffa_min' },
    { ...TALENTI, label: 'Tariffa max (€/giorno)', param: 'tariffa_max' },
    { ...AZIENDE, label: 'Budget min (€/giorno)', param: 'budget_min' },
    { ...AZIENDE, label: 'Budget max (€/giorno)', param: 'budget_max' },
  ])(
    '«1.500» in «$label» survives a reload of the address the app wrote',
    async ({ path, heading, label, endpoint, param }) => {
      const spy = vi
        .spyOn(globalThis, 'fetch')
        .mockImplementation(async () => answer(200, { totale: 0, items: [], per_stato: {} }))
      const router = mount(path)
      await screen.findByRole('heading', { name: heading })
      await userEvent.type(screen.getByLabelText(label), '1.500')
      await waitFor(() => expect(router.state.location.search).toMatchObject({ [param]: '1.500' }))
      // The router writes a string JSON would read as a number in quotes, so a reload,
      // a shared link or back/forward reads «1.500» back, never the number 1.5.
      const href = router.state.location.href
      expect(href).toContain(`${param}=%221.500%22`)

      cleanup()
      spy.mockClear()
      mount(href)
      await screen.findByRole('heading', { name: heading })
      expect(screen.getByLabelText(label)).toHaveValue('1.500')
      await waitFor(() => expect(listCalls(spy, endpoint).at(-1)).toContain(`${param}=1500.00`))
    },
  )

  it.each([
    { ...TALENTI, label: 'Tariffa min (€/giorno)', param: 'tariffa_min' },
    { ...TALENTI, label: 'Tariffa max (€/giorno)', param: 'tariffa_max' },
    { ...AZIENDE, label: 'Budget min (€/giorno)', param: 'budget_min' },
    { ...AZIENDE, label: 'Budget max (€/giorno)', param: 'budget_max' },
  ])(
    'reads a hand-typed «$param=1.500» as 1500 on a fresh load (REB-531)',
    async ({ path, heading, label, endpoint, param }) => {
      // Typed or pasted by hand, the value is not in quotes: the default parseSearch
      // would read it as the number 1.5 before the page ever saw «1.500».
      const spy = vi
        .spyOn(globalThis, 'fetch')
        .mockImplementation(async () => answer(200, { totale: 0, items: [], per_stato: {} }))
      mount(`${path}?${param}=1.500`)
      await screen.findByRole('heading', { name: heading })
      expect(screen.getByLabelText(label)).toHaveValue('1.500')
      await waitFor(() => expect(listCalls(spy, endpoint).at(-1)).toContain(`${param}=1500.00`))
    },
  )

  it('says a filter that is not an amount is not one, and narrows nothing with it', async () => {
    const spy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async () => answer(200, { totale: 0, items: [], per_stato: {} }))
    mount('/admin/talent')
    await screen.findByRole('heading', { name: 'Talenti' })

    await userEvent.type(screen.getByLabelText('Tariffa min (€/giorno)'), 'tanto')

    expect(await screen.findByRole('alert')).toHaveTextContent('Serve una cifra, in euro.')
    expect(screen.getByLabelText('Tariffa min (€/giorno)')).toHaveAttribute('aria-invalid', 'true')
    expect(listCalls(spy, '/api/hub/talent').every((url) => !url.includes('tariffa_min'))).toBe(true)
  })
})

describe('two empty states, in Italian (REB-286)', () => {
  it.each([
    ['/admin/talent?tariffa_min=tanto', 'Tariffa min (€/giorno)', 'Nessun profilo qui.'],
    ['/admin/companies?budget_min=tanto', 'Budget min (€/giorno)', 'Nessuna richiesta qui.'],
  ])('does not count an amount it cannot send as a filter: %s (REB-485)', async (path, label, empty) => {
    const spy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async () => answer(200, { totale: 0, items: [], per_stato: {} }))
    mount(path)
    expect(await screen.findByText(empty)).toBeInTheDocument()
    expect(screen.getByLabelText(label)).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('alert')).toHaveTextContent('Serve una cifra, in euro.')
    expect(spy.mock.calls.every(([url]) => !String(url).includes('_min'))).toBe(true)
  })
})
