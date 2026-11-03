import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { KanbanBoard, resolveMove } from './KanbanBoard'
import type { Deal } from './queries'

const STAGES = [
  { id: 's1', nome: 'Lead', position: 0, default_probability: 10, type: 'open' as const, code: null },
  {
    id: 's2',
    nome: 'Offerta',
    position: 1,
    default_probability: 50,
    type: 'open' as const,
    code: null,
  },
  { id: 's3', nome: 'Vinto', position: 2, default_probability: 100, type: 'won' as const, code: null },
]

const DEALS = [
  { id: 'd1', nome: 'Progetto A', pipeline_stage_id: 's1', expected_value: '1000.00', probability: 10 },
  { id: 'd2', nome: 'Progetto B', pipeline_stage_id: 's2', expected_value: '2500.50', probability: 50 },
] as unknown as Deal[]

describe('KanbanBoard', () => {
  it('renders one column per stage', () => {
    render(<KanbanBoard stages={STAGES} deals={DEALS} onMove={vi.fn()} onOpen={vi.fn()} />)
    for (const stage of ['Lead', 'Offerta', 'Vinto']) {
      expect(screen.getByText(stage)).toBeInTheDocument()
    }
  })

  it('places each deal in its own stage', () => {
    render(<KanbanBoard stages={STAGES} deals={DEALS} onMove={vi.fn()} onOpen={vi.fn()} />)
    expect(screen.getByText('Progetto A')).toBeInTheDocument()
    expect(screen.getByText('Progetto B')).toBeInTheDocument()
  })

  it('shows the total value per column in euros, with a thousands separator', () => {
    // `/2\.500,50/` -- with the dot -- is only reachable with `useGrouping:
    // 'always'` on the formatter behind this: it-IT's own default grouping
    // withholds the thousands separator below five integer digits, so a bare
    // `Intl.NumberFormat('it-IT', {style:'currency',currency:'EUR'})` (no
    // `useGrouping`) renders "2500,50 €" for this exact value, no dot at all --
    // checked directly against this stack's ICU. This assertion would fail
    // against that unqualified formatter, which is exactly what the brief's own
    // sample `KanbanCard`/`KanbanBoard` used.
    //
    // Two elements legitimately match: the "Offerta" column has exactly one
    // deal, so its own card and the column's total show the identical grouped
    // string. `getAllByText` (not `getByText`) is deliberate here, not a
    // weakened assertion -- see the next test for the case with more than one
    // deal in a column, where the total and each card's own value differ.
    render(<KanbanBoard stages={STAGES} deals={DEALS} onMove={vi.fn()} onOpen={vi.fn()} />)
    expect(screen.getAllByText(/2\.500,50/)).toHaveLength(2)
  })

  it('shows a per-column count', () => {
    render(<KanbanBoard stages={STAGES} deals={DEALS} onMove={vi.fn()} onOpen={vi.fn()} />)
    expect(screen.getAllByText('1').length).toBeGreaterThanOrEqual(2)
  })

  it('renders empty stages too, so the pipeline shape stays visible', () => {
    render(<KanbanBoard stages={STAGES} deals={[]} onMove={vi.fn()} onOpen={vi.fn()} />)
    expect(screen.getAllByText('Nessun deal')).toHaveLength(3)
  })

  it('sums more than one deal in the same column, not just displays a single value', () => {
    const sameColumn = [
      { id: 'a', nome: 'A', pipeline_stage_id: 's1', expected_value: '1000.00', probability: 10 },
      { id: 'b', nome: 'B', pipeline_stage_id: 's1', expected_value: '2500.50', probability: 10 },
    ] as unknown as Deal[]
    render(<KanbanBoard stages={STAGES} deals={sameColumn} onMove={vi.fn()} onOpen={vi.fn()} />)
    expect(screen.getByText(/3\.500,50/)).toBeInTheDocument()
  })

  it('opens a deal when its card is clicked', async () => {
    const onOpen = vi.fn()
    render(<KanbanBoard stages={STAGES} deals={DEALS} onMove={vi.fn()} onOpen={onOpen} />)
    await userEvent.click(screen.getByText('Progetto A'))
    expect(onOpen).toHaveBeenCalledWith('d1')
  })

  it('does not render the grab cursor on a card when dragging is disabled', () => {
    render(
      <KanbanBoard stages={STAGES} deals={DEALS} onMove={vi.fn()} onOpen={vi.fn()} canDrag={false} />,
    )
    const card = screen.getByText('Progetto A').closest('.cursor-grab')
    expect(card).toBeNull()
  })
})

/**
 * `resolveMove` is `KanbanBoard`'s own drag-end decision (see its docstring in
 * KanbanBoard.tsx for why it is a plain, directly-testable function rather than
 * something exercised through a simulated pointer gesture: jsdom has no real
 * layout for dnd-kit's sensors to measure).
 */
describe('resolveMove', () => {
  it('moves a deal to the column it was dropped on', () => {
    expect(resolveMove(DEALS, 'd1', 's2')).toEqual({ dealId: 'd1', stageId: 's2' })
  })

  it('does nothing when dropped back on the same column it started in', () => {
    expect(resolveMove(DEALS, 'd1', 's1')).toBeNull()
  })

  it('does nothing when dropped outside any column', () => {
    expect(resolveMove(DEALS, 'd1', undefined)).toBeNull()
  })

  it('does nothing for a dragged id that no longer matches a known deal', () => {
    expect(resolveMove(DEALS, 'ghost', 's2')).toBeNull()
  })
})
