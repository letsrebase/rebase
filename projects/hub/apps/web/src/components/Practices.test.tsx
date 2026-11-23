import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { WorkProfilePractice } from '@/lib/api'
import { PracticeRows, PracticeTable } from './Practices'

const row = (id: string, seen: number, of: number, products: number, months: number, method: 'code' | 'model'): WorkProfilePractice => ({
  id,
  label: id === 'checks_before_commit' ? 'Verifica prima del commit' : 'Chiede alternative',
  method,
  seen,
  of,
  products,
  months,
  classified_by: null,
})

describe('PracticeRows', () => {
  it('renders one row per practice in list order, with the label, the counts and the mark', () => {
    render(<PracticeRows labelledBy="h" practices={[row('checks_before_commit', 18, 25, 3, 4, 'code'), row('asks_for_alternatives', 11, 60, 4, 5, 'model')]} />)
    expect(screen.getByText('Verifica prima del commit')).toBeInTheDocument()
    expect(screen.getByText(/in 18 sessioni su 25/)).toBeInTheDocument()
    expect(screen.getByText(/3 prodotti/)).toBeInTheDocument()
    expect(screen.getByText(/4 mesi/)).toBeInTheDocument()
    expect(screen.getByText('contato dal codice')).toBeInTheDocument()
    expect(screen.getByText('dal modello della persona, non ricontrollato')).toBeInTheDocument()
  })

  it('is named by the heading it sits under', () => {
    render(
      <>
        <h4 id="h">Come lavora</h4>
        <PracticeRows labelledBy="h" practices={[row('checks_before_commit', 18, 25, 3, 4, 'code')]} />
      </>,
    )
    expect(screen.getByRole('list', { name: 'Come lavora' })).toBeInTheDocument()
  })

  it('ends the counts with a full stop so each part is read on its own', () => {
    render(<PracticeRows labelledBy="h" practices={[row('checks_before_commit', 18, 25, 3, 4, 'code')]} />)
    expect(screen.getByText(/4 mesi\.$/)).toBeInTheDocument()
  })

  it('uses the singular for one', () => {
    render(<PracticeRows labelledBy="h" practices={[row('checks_before_commit', 1, 5, 1, 1, 'code')]} />)
    expect(screen.getByText(/in 1 sessione su 5, 1 prodotto, 1 mese\.$/)).toBeInTheDocument()
  })

  it('has no bar, no meter and no total', () => {
    render(<PracticeRows labelledBy="h" practices={[row('checks_before_commit', 18, 25, 3, 4, 'code')]} />)
    expect(screen.queryByRole('progressbar')).toBeNull()
    expect(screen.queryByRole('meter')).toBeNull()
    expect(screen.queryByText(/totale/i)).toBeNull()
  })

  it('renders nothing for an empty list', () => {
    const { container } = render(<PracticeRows labelledBy="h" practices={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('appends the agent and model to the mark of model rows only', () => {
    const by = { agent: 'claude-code', model: 'claude-opus-4' }
    render(<PracticeRows labelledBy="h" practices={[{ ...row('checks_before_commit', 18, 25, 3, 4, 'code'), classified_by: null }, { ...row('asks_for_alternatives', 11, 60, 4, 5, 'model'), classified_by: by }]} />)
    expect(screen.getByText('dal modello della persona, non ricontrollato · Claude Code, claude-opus-4')).toBeInTheDocument()
    expect(screen.getByText('contato dal codice')).toBeInTheDocument()
  })

  it('shows only the agent when the model is null, and an unknown agent as it is', () => {
    render(<PracticeRows labelledBy="h" practices={[{ ...row('asks_for_alternatives', 11, 60, 4, 5, 'model'), classified_by: { agent: 'newagent', model: null } }]} />)
    expect(screen.getByText('dal modello della persona, non ricontrollato · newagent')).toBeInTheDocument()
  })
})

function cells(name: string): string[] {
  const line = screen.getByRole('row', { name: new RegExp(name) })
  return within(line)
    .getAllByRole('cell')
    .map((cell) => cell.textContent ?? '')
}

describe('PracticeTable', () => {
  it('renders one row per practice in list order: the practice, «N su M», products, months and the mark', () => {
    render(
      <>
        <h4 id="h">Come lavora</h4>
        <PracticeTable labelledBy="h" practices={[row('checks_before_commit', 18, 25, 3, 4, 'code'), row('asks_for_alternatives', 11, 60, 4, 5, 'model')]} />
      </>,
    )
    expect(screen.getAllByRole('columnheader').map((head) => head.textContent)).toEqual([
      'Pratica',
      'Sessioni',
      'Prodotti',
      'Mesi',
      'Contata da',
    ])
    expect(cells('Verifica prima del commit')).toEqual(['Verifica prima del commit', '18 su 25', '3', '4', 'contato dal codice'])
    expect(cells('Chiede alternative')).toEqual([
      'Chiede alternative',
      '11 su 60',
      '4',
      '5',
      'dal modello della persona, non ricontrollato',
    ])
  })

  it('is named by the heading it sits under', () => {
    render(
      <>
        <h4 id="h">Come lavora</h4>
        <PracticeTable labelledBy="h" practices={[row('checks_before_commit', 18, 25, 3, 4, 'code')]} />
      </>,
    )
    expect(screen.getByRole('table', { name: 'Come lavora' })).toBeInTheDocument()
  })

  it('folds the practices never seen into one muted row, named', () => {
    render(
      <PracticeTable
        labelledBy="h"
        practices={[row('checks_before_commit', 18, 25, 3, 4, 'code'), row('asks_for_alternatives', 0, 60, 0, 0, 'model')]}
      />,
    )
    expect(screen.getAllByRole('row')).toHaveLength(3)
    expect(screen.getByText('Mai viste: Chiede alternative.')).toBeInTheDocument()
    expect(screen.queryByText('0 su 60')).toBeNull()
  })

  it('has no bar, no meter and no total', () => {
    render(<PracticeTable labelledBy="h" practices={[row('checks_before_commit', 18, 25, 3, 4, 'code')]} />)
    expect(screen.queryByRole('progressbar')).toBeNull()
    expect(screen.queryByRole('meter')).toBeNull()
    expect(screen.queryByText(/totale/i)).toBeNull()
  })

  it('renders nothing for an empty list', () => {
    const { container } = render(<PracticeTable labelledBy="h" practices={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('appends the agent and model to the mark of model rows only', () => {
    const by = { agent: 'claude-code', model: 'claude-opus-4' }
    render(<PracticeTable labelledBy="h" practices={[{ ...row('checks_before_commit', 18, 25, 3, 4, 'code'), classified_by: null }, { ...row('asks_for_alternatives', 11, 60, 4, 5, 'model'), classified_by: by }]} />)
    expect(screen.getByText('dal modello della persona, non ricontrollato · Claude Code, claude-opus-4')).toBeInTheDocument()
    expect(screen.getByText('contato dal codice')).toBeInTheDocument()
  })

  it('shows only the agent when the model is null, and an unknown agent as it is', () => {
    render(<PracticeTable labelledBy="h" practices={[{ ...row('asks_for_alternatives', 11, 60, 4, 5, 'model'), classified_by: { agent: 'newagent', model: null } }]} />)
    expect(screen.getByText('dal modello della persona, non ricontrollato · newagent')).toBeInTheDocument()
  })
})
