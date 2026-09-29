import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from './dialog'

function renderDialog(props: { closeLabel?: string; footer?: boolean } = {}) {
  return render(
    <Dialog open>
      <DialogContent closeLabel={props.closeLabel}>
        <DialogTitle>Modifica scheda</DialogTitle>
        <DialogDescription>Le righe che il freelance vede.</DialogDescription>
        {props.footer && <DialogFooter showCloseButton closeLabel={props.closeLabel} />}
      </DialogContent>
    </Dialog>,
  )
}

/**
 * Every rebase product speaks Italian to its users, so the X in the corner and the
 * footer's own button say «Chiudi» to a screen reader and on screen, never shadcn's
 * «Close» (REB-593). A page with a better word passes `closeLabel`.
 */
describe('Dialog', () => {
  it('closes with «Chiudi», in the corner and in the footer', () => {
    renderDialog({ footer: true })
    screen.getByRole('dialog', { name: 'Modifica scheda' })
    expect(screen.getAllByRole('button', { name: 'Chiudi' })).toHaveLength(2)
    expect(screen.queryByRole('button', { name: 'Close' })).toBeNull()
  })

  it('takes the page’s own word for the close control', () => {
    renderDialog({ closeLabel: 'Annulla', footer: true })
    expect(screen.getAllByRole('button', { name: 'Annulla' })).toHaveLength(2)
    expect(screen.queryByRole('button', { name: 'Chiudi' })).toBeNull()
  })
})
