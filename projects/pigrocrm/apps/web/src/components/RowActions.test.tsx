import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { RowActions } from './RowActions'

describe('RowActions', () => {
  it('shows one «⋯» button, labelled for assistive technology, and no menu until asked', () => {
    render(<RowActions items={[{ label: 'Apri', onSelect: vi.fn() }]} />)
    expect(screen.getByRole('button', { name: 'Azioni' })).toBeInTheDocument()
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('accepts a more specific label than the default', () => {
    render(<RowActions label="Azioni sulla fattura" items={[{ label: 'Apri', onSelect: vi.fn() }]} />)
    expect(screen.getByRole('button', { name: 'Azioni sulla fattura' })).toBeInTheDocument()
  })

  /** A row with nothing to do must not grow a button that opens an empty menu: the
   *  «⋯» is a promise that there is something behind it. */
  it('renders nothing at all when there are no actions', () => {
    const { container } = render(<RowActions items={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('opens the menu with one item per action and runs the one that is chosen', async () => {
    const open = vi.fn()
    const elimina = vi.fn()
    render(
      <RowActions
        items={[
          { label: 'Apri', onSelect: open },
          { label: 'Elimina', onSelect: elimina, destructive: true },
        ]}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Azioni' }))
    expect(screen.getByRole('menuitem', { name: 'Apri' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('menuitem', { name: 'Elimina' }))
    expect(elimina).toHaveBeenCalledOnce()
    expect(open).not.toHaveBeenCalled()
  })

  it('marks a destructive action as such rather than letting it read like the others', async () => {
    render(<RowActions items={[{ label: 'Elimina', onSelect: vi.fn(), destructive: true }]} />)
    await userEvent.click(screen.getByRole('button', { name: 'Azioni' }))
    expect(screen.getByRole('menuitem', { name: 'Elimina' })).toHaveAttribute(
      'data-variant',
      'destructive',
    )
  })

  it('disables an action that is not available right now instead of hiding it', async () => {
    const emetti = vi.fn()
    render(<RowActions items={[{ label: 'Emetti', onSelect: emetti, disabled: true }]} />)
    await userEvent.click(screen.getByRole('button', { name: 'Azioni' }))
    const item = screen.getByRole('menuitem', { name: 'Emetti' })
    expect(item).toHaveAttribute('data-disabled')
    await userEvent.click(item)
    expect(emetti).not.toHaveBeenCalled()
  })

  /**
   * The whole table row is clickable (`DataTable`'s `onRowClick` opens the record), and
   * the menu content is rendered through a React portal -- so a synthetic click on the
   * trigger, or on an item, still bubbles up the *React* tree to the row's handler even
   * though it is nowhere near it in the DOM. Without stopping it, pressing «⋯» would
   * navigate away from the table before the menu could be read.
   */
  it('never lets the row underneath it react to the menu', async () => {
    const rowClick = vi.fn()
    const open = vi.fn()
    render(
      <div onClick={rowClick}>
        <RowActions items={[{ label: 'Apri', onSelect: open }]} />
      </div>,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Azioni' }))
    expect(rowClick).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('menuitem', { name: 'Apri' }))
    expect(open).toHaveBeenCalledOnce()
    expect(rowClick).not.toHaveBeenCalled()
  })

  /** `DataTable` also activates a row on Enter and Space so the keyboard can reach it.
   *  Both keys open this menu, which must not double as "open the record" -- so each
   *  case asserts both halves: the menu opened, *and* the row stayed asleep. Asserting
   *  only the second half would go green if keyboard opening broke altogether. */
  it('opens on Enter without activating the row underneath', async () => {
    const rowKeyDown = vi.fn()
    render(
      <div onKeyDown={rowKeyDown}>
        <RowActions items={[{ label: 'Apri', onSelect: vi.fn() }]} />
      </div>,
    )
    screen.getByRole('button', { name: 'Azioni' }).focus()
    await userEvent.keyboard('{Enter}')
    expect(screen.getByRole('menu')).toBeInTheDocument()
    expect(rowKeyDown).not.toHaveBeenCalled()
  })

  it('opens on Space without activating the row underneath', async () => {
    const rowKeyDown = vi.fn()
    render(
      <div onKeyDown={rowKeyDown}>
        <RowActions items={[{ label: 'Apri', onSelect: vi.fn() }]} />
      </div>,
    )
    screen.getByRole('button', { name: 'Azioni' }).focus()
    await userEvent.keyboard('{ }')
    expect(screen.getByRole('menu')).toBeInTheDocument()
    expect(rowKeyDown).not.toHaveBeenCalled()
  })

  /**
   * The whole path this component's docstring promises, from the keyboard: open, walk to
   * an item, run it -- and none of it reaching the row. This is the case the React portal
   * makes non-obvious, because Radix's own activation of an item dispatches a click that
   * bubbles up the React tree from outside the row's DOM subtree.
   */
  it('runs an action chosen entirely from the keyboard, once, and never wakes the row', async () => {
    const rowClick = vi.fn()
    const open = vi.fn()
    /* The row activates on Enter, on Space and on a click, and on nothing else (see
       `DataTable`'s `handleRowKeyDown`). So the invariant to hold is not "no key ever
       reaches the row" -- an arrow pressed inside an open menu bubbling past it is
       harmless, and Radix's own roving focus needs the arrows to keep bubbling inside
       the portal -- but "none of the keys the row acts on reaches it". */
    const rowKeys: string[] = []
    render(
      <div onClick={rowClick} onKeyDown={(event) => rowKeys.push(event.key)}>
        <RowActions items={[{ label: 'Apri', onSelect: open }]} />
      </div>,
    )
    screen.getByRole('button', { name: 'Azioni' }).focus()
    await userEvent.keyboard('{Enter}')
    await userEvent.keyboard('{ArrowDown}{Enter}')
    expect(open).toHaveBeenCalledOnce()
    expect(rowClick).not.toHaveBeenCalled()
    expect(rowKeys).not.toContain('Enter')
    expect(rowKeys).not.toContain(' ')
  })
})
