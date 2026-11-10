import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { expenseNoteKeys, type ExpenseNoteSummary } from '@/features/expense-notes/queries'
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

vi.mock('@/features/expense-notes/NewNote', () => ({
  NewNote: ({ open }: { open: boolean }) => (open ? <div>Form della nota spese</div> : null),
}))

const summary = (overrides: Partial<ExpenseNoteSummary> = {}): ExpenseNoteSummary => ({
  staff_member_id: null,
  staff_member_name: null,
  staff_member_legal_entity_id: null,
  can_submit: false,
  to_approve: 0,
  to_reimburse: 0,
  closed: 0,
  awaiting_reimbursement: '0.00',
  only_administrator: false,
  ...overrides,
})
const LINKED = summary({ staff_member_id: 'm-1', staff_member_name: 'Giulia Bianchi', can_submit: true })

function renderQuickAdd(off: readonly ModuleKey[] = [], notes: ExpenseNoteSummary = summary()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  seedModules(client, off)
  // Who the caller is among the staff, as the server would answer; seeded so nothing is asked.
  client.setQueryData(expenseNoteKeys.summary, notes)
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

  describe('«Nota spese» (REB-1046)', () => {
    it('sits after «Spesa» and before «Carica un documento» for a user linked to a staff member', () => {
      const sheet = renderQuickAdd([], LINKED)
      expect(labels(sheet)).toEqual([
        'AttivitàUna cosa da fare, con una scadenza',
        'NotaSu un cliente, una persona o un deal',
        'GiornataUn giorno lavorato su un contratto',
        'OreOre lavorate su un deal',
        'SpesaUno scontrino o una spesa, in due tocchi',
        'Nota speseUna spesa o dei chilometri da farti rimborsare',
        'Carica un documentoUna foto o un file, in In arrivo',
      ])
    })

    it.each([
      ['not linked to a staff member', [], summary({ can_submit: true })],
      ['readonly, who cannot send', [], summary({ staff_member_id: 'm-1', can_submit: false })],
      ['staff off', ['staff'], LINKED],
      ['purchases off', ['purchases'], LINKED],
    ] as const)('is not drawn when %s', (_name, off, notes) => {
      const sheet = renderQuickAdd(off, notes)
      expect(labels(sheet).some((text) => text?.startsWith('Nota spese'))).toBe(false)
      expect(labels(sheet).some((text) => text?.startsWith('Carica un documento'))).toBe(true)
    })

    it('opens the form of the note', async () => {
      const sheet = renderQuickAdd([], LINKED)
      await userEvent.click(within(sheet).getByRole('button', { name: /^Nota spese/ }))
      expect(await screen.findByText('Form della nota spese')).toBeInTheDocument()
    })
  })
})
