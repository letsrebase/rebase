import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { ModulesPanel } from './ModulesPanel'

const mockGet = vi.spyOn(api, 'GET')
const mockPut = vi.spyOn(api, 'PUT')

afterEach(() => {
  mockGet.mockReset()
  mockPut.mockReset()
})

function ok(data: unknown) {
  return Promise.resolve({ data, response: new Response(null, { status: 200 }) } as never)
}

function module(
  key: string,
  label: string,
  over: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    key,
    label,
    description: `Descrizione di ${label}`,
    requires: [],
    enabled: true,
    stored: true,
    default: true,
    holds_data: false,
    records: 0,
    ...over,
  }
}

function state(over: { deals?: Record<string, unknown>; time?: Record<string, unknown> } = {}) {
  return {
    profile: 'company',
    modules: [
      module('deals', 'Deal', { holds_data: true, records: 4, ...over.deals }),
      module('contracts', 'Contratti'),
      module('time', 'Ore', { requires: ['deals'], holds_data: true, records: 12, ...over.time }),
      module('purchases', 'Acquisti'),
      module('taxes', 'Tasse'),
    ],
  }
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ModulesPanel />
    </QueryClientProvider>,
  )
}

describe('ModulesPanel', () => {
  it('lists each module with its description and the line about data that stays', async () => {
    mockGet.mockReturnValue(ok(state()))
    renderPanel()
    expect(await screen.findByText('Descrizione di Deal')).toBeInTheDocument()
    expect(screen.getAllByText(/I dati restano: riaccendendolo li ritrovi/)).toHaveLength(2)
    expect(screen.getByRole('checkbox', { name: 'Contratti' })).toBeChecked()
  })

  it('switches deals off with time on by listing time and sending both, then back on', async () => {
    const user = userEvent.setup()
    mockGet.mockReturnValue(ok(state()))
    mockPut.mockReturnValue(
      ok(state({ deals: { enabled: false }, time: { enabled: false } })),
    )
    renderPanel()
    await user.click(await screen.findByRole('checkbox', { name: 'Deal' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: 'Spegnere Deal?' })).toBeInTheDocument()
    const involved = within(dialog).getByRole('list', { name: 'Moduli coinvolti' })
    expect(within(involved).getByText('Ore')).toBeInTheDocument()
    expect(mockPut).not.toHaveBeenCalled()

    await user.click(within(dialog).getByRole('button', { name: 'Spegni' }))
    await waitFor(() =>
      expect(mockPut).toHaveBeenCalledWith('/api/settings/modules', {
        body: { off: ['deals', 'time'], confirm_off: ['deals', 'time'] },
      }),
    )

    // Switched on again from the same page: Ore needs Deal, so turning Ore on lists Deal.
    mockGet.mockReset()
    mockPut.mockReset()
    mockPut.mockReturnValue(ok(state()))
    await user.click(await screen.findByRole('checkbox', { name: 'Deal' }))
    await waitFor(() =>
      expect(mockPut).toHaveBeenCalledWith('/api/settings/modules', { body: { on: ['deals'] } }),
    )
  })

  it('switches a module with no data and no dependants off without asking', async () => {
    const user = userEvent.setup()
    mockGet.mockReturnValue(ok(state()))
    mockPut.mockReturnValue(ok(state()))
    renderPanel()
    await user.click(await screen.findByRole('checkbox', { name: 'Acquisti' }))
    await waitFor(() =>
      expect(mockPut).toHaveBeenCalledWith('/api/settings/modules', { body: { off: ['purchases'] } }),
    )
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows the module diff of a profile change before anything is written', async () => {
    const user = userEvent.setup()
    mockGet.mockReturnValue(ok(state()))
    mockPut.mockImplementation(((path: string, init: { body: { confirm: boolean } }) => {
      if (path === '/api/settings/modules/profile' && !init.body.confirm) {
        return ok({
          profile: 'solo',
          previous_profile: 'company',
          applied: false,
          state: state(),
          changes: [
            { module: 'deals', label: 'Deal', was: true, becomes: false, records: 4 },
            { module: 'time', label: 'Ore', was: true, becomes: false, records: 12 },
          ],
        })
      }
      return ok({ profile: 'solo', previous_profile: 'company', applied: true, changes: [], state: state() })
    }) as never)
    renderPanel()
    await user.click(await screen.findByRole('radio', { name: /Da solo/ }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('4 record', { exact: false })).toBeInTheDocument()
    expect(mockPut).toHaveBeenCalledTimes(1)

    // Keeping Ore on while Deal goes off breaks a dependency: the server would refuse it, so
    // the page says so and holds Conferma back until Deal is kept too.
    await user.click(within(dialog).getByRole('checkbox', { name: /Ore/ }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent('Ore richiede Deal')
    expect(within(dialog).getByRole('button', { name: 'Conferma' })).toBeDisabled()
    await user.click(within(dialog).getByRole('checkbox', { name: /Deal/ }))
    await user.click(within(dialog).getByRole('button', { name: 'Conferma' }))
    await waitFor(() =>
      expect(mockPut).toHaveBeenLastCalledWith('/api/settings/modules/profile', {
        body: { profile: 'solo', confirm: true, modules: { time: true, deals: true } },
      }),
    )
  })
})
