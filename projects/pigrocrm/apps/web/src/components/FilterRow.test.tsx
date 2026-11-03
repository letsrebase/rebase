import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { FilterChip, FilterChips, FilterRow } from './FilterRow'

describe('FilterChip', () => {
  it('reports whether it is on through aria-pressed, not through a class', () => {
    // The chip is a toggle, so a screen reader has to be able to hear that it is on.
    // `aria-pressed` is also what the page tests read, precisely so a restyle cannot
    // break them.
    render(
      <FilterChip pressed onPress={vi.fn()}>
        Bozza
      </FilterChip>,
    )
    expect(screen.getByRole('button', { name: 'Bozza' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('is off by default', () => {
    render(
      <FilterChip pressed={false} onPress={vi.fn()}>
        Bozza
      </FilterChip>,
    )
    expect(screen.getByRole('button', { name: 'Bozza' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('calls back on click', async () => {
    const onPress = vi.fn()
    render(
      <FilterChip pressed={false} onPress={onPress}>
        Bozza
      </FilterChip>,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Bozza' }))
    expect(onPress).toHaveBeenCalledOnce()
  })
})

describe('FilterChips', () => {
  const STATES = [
    { value: 'draft', label: 'Bozza' },
    { value: 'issued', label: 'Emessa' },
  ]

  it('renders «Tutte» plus one chip per option, with «Tutte» on when nothing is selected', () => {
    render(<FilterChips label="Filtra per stato" allLabel="Tutte" options={STATES} value={null} onChange={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Tutte' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Bozza' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('group', { name: 'Filtra per stato' })).toBeInTheDocument()
  })

  it('selects an option, and deselects it when it is pressed again', async () => {
    const onChange = vi.fn()
    const { rerender } = render(
      <FilterChips label="Filtra per stato" allLabel="Tutte" options={STATES} value={null} onChange={onChange} />,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Emessa' }))
    expect(onChange).toHaveBeenLastCalledWith('issued')

    // A chip is a toggle and not a radio: pressing the one that is already on clears
    // the filter, which is the same thing «Tutte» does and the reason both work.
    rerender(
      <FilterChips label="Filtra per stato" allLabel="Tutte" options={STATES} value="issued" onChange={onChange} />,
    )
    expect(screen.getByRole('button', { name: 'Emessa' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Tutte' })).toHaveAttribute('aria-pressed', 'false')
    await userEvent.click(screen.getByRole('button', { name: 'Emessa' }))
    expect(onChange).toHaveBeenLastCalledWith(null)
  })

  it('clears the filter from «Tutte»', async () => {
    const onChange = vi.fn()
    render(
      <FilterChips label="Filtra per stato" allLabel="Tutte" options={STATES} value="draft" onChange={onChange} />,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Tutte' }))
    expect(onChange).toHaveBeenLastCalledWith(null)
  })
})

describe('FilterRow', () => {
  it('groups its controls under one landmark a page test can scope to', () => {
    render(
      <FilterRow>
        <FilterChip pressed={false} onPress={vi.fn()}>
          Bozza
        </FilterChip>
      </FilterRow>,
    )
    expect(screen.getByRole('search')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Bozza' })).toBeInTheDocument()
  })
})
