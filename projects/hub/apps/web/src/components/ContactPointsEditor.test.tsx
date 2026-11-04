import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import type { ContactPoint } from '@/lib/api'
import { draftsFrom, toWrite, type PointDraft } from '@/lib/contactPoints'
import { ContactPointsEditor } from './ContactPointsEditor'
import { ContactPointsList } from './ContactPointsList'

const POINTS: ContactPoint[] = [
  { id: null, kind: 'email', value: 'ada@studio.it', label: 'work', note: null, is_primary: true, position: 0 },
  { id: 'p1', kind: 'phone', value: '+39 333 111 2222', label: 'work', note: null, is_primary: true, position: 1 },
  { id: 'p2', kind: 'phone', value: '02 5550199', label: 'billing', note: 'centralino', is_primary: false, position: 2 },
]

/** The editor as a caller holds it: the drafts in state, and what would be sent shown out. */
function Harness({ points = POINTS }: { points?: ContactPoint[] }) {
  const [drafts, setDrafts] = useState<PointDraft[]>(() => draftsFrom(points))
  return (
    <>
      <ContactPointsEditor idPrefix="t" login="ada@studio.it" drafts={drafts} onChange={setDrafts} />
      <output aria-label="sent">{JSON.stringify(toWrite(drafts))}</output>
    </>
  )
}

function sent(): unknown[] {
  return JSON.parse(screen.getByLabelText('sent').textContent ?? '[]') as unknown[]
}

describe('ContactPointsEditor', () => {
  it('shows the sign-in address on top, read only and not among the editable rows', () => {
    render(<Harness />)
    expect(screen.getByText('ada@studio.it')).toBeInTheDocument()
    expect(screen.getByText(/Non si cambia da qui/)).toBeInTheDocument()
    expect(screen.getAllByRole('textbox', { name: /Valore del recapito/ })).toHaveLength(2)
  })

  it('adds a phone with a label and a note', async () => {
    render(<Harness points={[POINTS[0]!]} />)
    expect(screen.getByText('Nessun altro recapito.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Aggiungi recapito' }))
    await userEvent.type(screen.getByRole('textbox', { name: 'Valore del recapito 1' }), '+39 345 9876543')
    await userEvent.click(screen.getByRole('combobox', { name: 'Etichetta del recapito 1' }))
    await userEvent.click(screen.getByRole('option', { name: 'personale' }))
    await userEvent.type(screen.getByRole('textbox', { name: 'Nota del recapito 1' }), 'solo WhatsApp')
    expect(sent()).toEqual([
      { kind: 'phone', value: '+39 345 9876543', label: 'personal', note: 'solo WhatsApp', is_primary: false },
    ])
  })

  it('keeps one primary per kind: marking one takes the mark off the other', async () => {
    render(<Harness />)
    expect(screen.getByRole('checkbox', { name: 'Recapito 1 principale' })).toBeChecked()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Recapito 2 principale' }))
    expect(screen.getByRole('checkbox', { name: 'Recapito 1 principale' })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Recapito 2 principale' })).toBeChecked()
    expect(sent().map((point) => (point as { is_primary: boolean }).is_primary)).toEqual([false, true])
  })

  it('never offers an extra address as primary, and drops the mark when a phone becomes one', async () => {
    render(<Harness />)
    await userEvent.click(screen.getByRole('combobox', { name: 'Tipo del recapito 1' }))
    await userEvent.click(screen.getByRole('option', { name: 'Email' }))
    expect(screen.queryByRole('checkbox', { name: 'Recapito 1 principale' })).toBeNull()
    expect(screen.getByText(/non è mai quello di accesso/)).toBeInTheDocument()
    expect((sent()[0] as { is_primary: boolean }).is_primary).toBe(false)
  })

  it('takes a point off the list', async () => {
    render(<Harness />)
    await userEvent.click(screen.getByRole('button', { name: 'Togli il recapito 1' }))
    expect(sent()).toEqual([
      { kind: 'phone', value: '02 5550199', label: 'billing', note: 'centralino', is_primary: false },
    ])
  })
})

describe('ContactPointsList', () => {
  it('lists every point by kind with its label and note, the primary marked', () => {
    render(<ContactPointsList points={POINTS} />)
    expect(screen.getByRole('link', { name: 'ada@studio.it' })).toHaveAttribute('href', 'mailto:ada@studio.it')
    expect(screen.getByRole('link', { name: '+39 333 111 2222' })).toHaveAttribute('href', 'tel:+393331112222')
    expect(screen.getByText(/\(amministrazione: centralino\)/)).toBeInTheDocument()
    expect(screen.getAllByText('principale')).toHaveLength(2)
  })

  it('answers a dash for nobody', () => {
    render(<ContactPointsList points={[]} />)
    expect(screen.getByText('—')).toBeInTheDocument()
  })
})
