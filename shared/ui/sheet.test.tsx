import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Sheet, SheetContent, SheetDescription, SheetTitle } from './sheet'

function renderSheet(closeLabel?: string) {
  return render(
    <Sheet open>
      <SheetContent closeLabel={closeLabel}>
        <SheetTitle>Menu</SheetTitle>
        <SheetDescription>Le pagine dell’area.</SheetDescription>
      </SheetContent>
    </Sheet>,
  )
}

/** The same contract as the dialog's (REB-593): the X says «Chiudi», or the page's word. */
describe('Sheet', () => {
  it('closes with «Chiudi»', () => {
    renderSheet()
    expect(screen.getByRole('button', { name: 'Chiudi' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Close' })).toBeNull()
  })

  it('takes the page’s own word for the close control', () => {
    renderSheet('Chiudi il menu')
    expect(screen.getByRole('button', { name: 'Chiudi il menu' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Chiudi' })).toBeNull()
  })
})
