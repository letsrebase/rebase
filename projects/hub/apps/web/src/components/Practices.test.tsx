import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { WorkProfilePractice } from '@/lib/api'
import { PracticeRows } from './Practices'

const row = (id: string, seen: number, of: number, products: number, months: number, method: 'code' | 'model'): WorkProfilePractice => ({
  id,
  label: id === 'checks_before_commit' ? 'Verifica prima del commit' : 'Chiede alternative',
  method,
  seen,
  of,
  products,
  months,
})

describe('PracticeRows', () => {
  it('renders one row per practice in list order, with the label, the counts and the mark', () => {
    render(<PracticeRows practices={[row('checks_before_commit', 18, 25, 3, 4, 'code'), row('asks_for_alternatives', 11, 60, 4, 5, 'model')]} />)
    expect(screen.getByText('Verifica prima del commit')).toBeInTheDocument()
    expect(screen.getByText(/in 18 sessioni su 25/)).toBeInTheDocument()
    expect(screen.getByText(/3 prodotti/)).toBeInTheDocument()
    expect(screen.getByText(/4 mesi/)).toBeInTheDocument()
    expect(screen.getByText('contato dal codice')).toBeInTheDocument()
    expect(screen.getByText('dal modello della persona, non ricontrollato')).toBeInTheDocument()
  })

  it('uses the singular for one', () => {
    render(<PracticeRows practices={[row('checks_before_commit', 1, 5, 1, 1, 'code')]} />)
    expect(screen.getByText(/in 1 sessione su 5, 1 prodotto, 1 mese$/)).toBeInTheDocument()
  })

  it('has no bar, no meter and no total', () => {
    render(<PracticeRows practices={[row('checks_before_commit', 18, 25, 3, 4, 'code')]} />)
    expect(screen.queryByRole('progressbar')).toBeNull()
    expect(screen.queryByRole('meter')).toBeNull()
    expect(screen.queryByText(/totale/i)).toBeNull()
  })

  it('renders nothing for an empty list', () => {
    const { container } = render(<PracticeRows practices={[]} />)
    expect(container).toBeEmptyDOMElement()
  })
})
