import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { CellDays, PanelDays } from './CalendarDays'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, params, search, children, ...rest }: { to: string; params?: Record<string, string>; search?: Record<string, string>; children: React.ReactNode; 'aria-label'?: string }) => (
    <a aria-label={rest['aria-label']} href={(params ? to.replace(/\$(\w+)/g, (_, key: string) => params[key] ?? '') : to) + (search ? `?${new URLSearchParams(search).toString()}` : '')}>
      {children}
    </a>
  ),
}))
let canWrite = true
vi.mock('@/lib/auth', () => ({ useCanWrite: () => canWrite }))

const worked = {
  id: 'd1',
  contract_id: 'c1',
  contract_titolo: 'Consulenza architetturale',
  customer_nome: 'Acme S.r.l.',
  quantita: '0.50',
  stato: 'lavorato_senza_approvazione',
}
const approved = { ...worked, id: 'd2', quantita: '1.00', stato: 'approvato', customer_nome: 'Borgo' }

describe('a calendar cell', () => {
  it('marks a day worked without approval in words, and an ordinary one only by its dot', () => {
    render(<CellDays days={[worked, approved]} />)
    expect(screen.getByText('senza approvazione')).toBeInTheDocument()
    expect(screen.getByText('½ g')).toBeInTheDocument()
    expect(screen.getByText('1 g')).toBeInTheDocument()
    expect(screen.getAllByText(/Acme|Borgo/)).toHaveLength(2)
    // Gold for the flagged one, ink for the settled one: the same tints as the pills.
    const dots = document.querySelectorAll('[data-tone]')
    expect([...dots].map((dot) => dot.getAttribute('data-tone'))).toEqual(['gold', 'ink'])
  })

  it('draws nothing for a date with no day', () => {
    const { container } = render(<CellDays days={[]} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('the day panel’s days', () => {
  it('links each day to its page with its state, and offers to record one on this date', () => {
    canWrite = true
    render(<PanelDays giorno="2026-03-02" days={[worked]} />)
    expect(screen.getByRole('link', { name: /Consulenza architetturale, Acme S.r.l., Mezza giornata, Lavorata senza approvazione/ })).toHaveAttribute(
      'href',
      '/app/days/d1',
    )
    expect(screen.getByRole('link', { name: /Registra una giornata/ })).toHaveAttribute('href', '/app/days/new?data=2026-03-02')
  })

  it('offers an empty date a day to record, and a reader only the reading', () => {
    canWrite = true
    const { unmount } = render(<PanelDays giorno="2026-03-03" days={[]} />)
    expect(screen.getByText('Nessuna giornata di contratto.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Registra una giornata/ })).toHaveAttribute('href', '/app/days/new?data=2026-03-03')
    unmount()
    canWrite = false
    render(<PanelDays giorno="2026-03-03" days={[]} />)
    expect(screen.queryByRole('link', { name: /Registra una giornata/ })).not.toBeInTheDocument()
  })
})
