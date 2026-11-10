import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ModuleKey } from '@/lib/modules'
import { seedModules } from '@/lib/modules.testing'
import { QuickAdd } from './QuickAdd'

vi.mock('@tanstack/react-router', () => ({
  useRouterState: () => ({ location: { pathname: '/app' } }),
  useNavigate: () => vi.fn(),
}))
vi.mock('@/features/days/DayForm', () => ({ DayForm: () => null }))
vi.mock('@/features/inbox/QuickUpload', () => ({ QuickUpload: () => null }))
vi.mock('@/features/tasks/TaskDialog', () => ({ TaskDialog: () => null }))
vi.mock('./QuickHours', () => ({ QuickHours: () => null }))
vi.mock('./QuickNote', () => ({ QuickNote: () => null }))
vi.mock('@/features/purchases/QuickExpense', () => ({
  QuickExpense: ({ open }: { open: boolean }) => (open ? <div>Sheet della spesa</div> : null),
}))

function renderQuickAdd(off: readonly ModuleKey[] = []) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  seedModules(client, off)
  render(
    <QueryClientProvider client={client}>
      <QuickAdd open onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  )
  return screen.getByRole('dialog', { name: 'Aggiungi' })
}

const labels = (sheet: HTMLElement) =>
  within(sheet)
    .getAllByRole('button')
    .map((button) => button.textContent)
    .filter((text) => text !== 'Chiudi')

describe('the «+» sheet', () => {
  it('lists «Spesa» between «Ore» and «Carica un documento»', () => {
    const sheet = renderQuickAdd()
    expect(labels(sheet)).toEqual([
      'AttivitàUna cosa da fare, con una scadenza',
      'NotaSu un cliente, una persona o un deal',
      'GiornataUn giorno lavorato su un contratto',
      'OreOre lavorate su un deal',
      'SpesaUno scontrino o una spesa, in due tocchi',
      'Carica un documentoUna foto o un file, in In arrivo',
    ])
  })

  it('has no «Spesa» with the purchases module off', () => {
    const sheet = renderQuickAdd(['purchases'])
    expect(labels(sheet).some((text) => text?.startsWith('Spesa'))).toBe(false)
    expect(labels(sheet).some((text) => text?.startsWith('Carica un documento'))).toBe(true)
  })

  it('keeps «Spesa» when hours and days are off: it follows its own module only', () => {
    const sheet = renderQuickAdd(['deals', 'contracts'])
    expect(labels(sheet).some((text) => text?.startsWith('Spesa'))).toBe(true)
  })

  it('opens the expense sheet from «Spesa»', async () => {
    const sheet = renderQuickAdd()
    await userEvent.click(within(sheet).getByRole('button', { name: /^Spesa/ }))
    expect(await screen.findByText('Sheet della spesa')).toBeInTheDocument()
  })
})
