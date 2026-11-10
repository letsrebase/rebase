import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { ContactPointsEditor } from './ContactPointsEditor'
import {
  pointErrors,
  pointsToBody,
  type ContactPointDraft,
} from './contactPoints'
import type { ProblemDetail } from '@/lib/api'

/** The editor is controlled, so it is mounted the way a form mounts it: the drafts in state,
 *  the errors computed from them, and the latest drafts exposed to the test. */
let latest: ContactPointDraft[] = []

function Harness({
  initial = [],
  problem = null,
  checked = false,
}: {
  initial?: ContactPointDraft[]
  problem?: ProblemDetail | null
  checked?: boolean
}) {
  const [points, setPoints] = useState(initial)
  return (
    <ContactPointsEditor
      points={points}
      onChange={(next) => {
        latest = next
        setPoints(next)
      }}
      errors={pointErrors(points, problem, checked)}
    />
  )
}

function stored(
  key: string,
  kind: ContactPointDraft['kind'],
  value: string,
  is_primary = false,
): ContactPointDraft {
  return { key, kind, value, label: 'work', note: '', is_primary }
}

function primaryBox(n: number) {
  return screen.getByLabelText(`Principale recapito ${n}`)
}

async function addRow(value: string) {
  await userEvent.click(screen.getByRole('button', { name: 'Aggiungi recapito' }))
  const rows = screen.getAllByRole('group')
  const last = rows[rows.length - 1] as HTMLElement
  await userEvent.type(within(last).getByLabelText(/^Valore/), value)
}

describe('ContactPointsEditor', () => {
  it('starts empty with a sentence, and an add button', () => {
    render(<Harness />)
    expect(screen.getByText(/Nessun recapito/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Aggiungi recapito' })).toBeEnabled()
  })

  it('makes the first point of a kind the primary and the second not', async () => {
    render(<Harness />)
    await addRow('a@x.it')
    await addRow('b@x.it')

    expect(primaryBox(1)).toBeChecked()
    expect(primaryBox(2)).not.toBeChecked()
  })

  it('moves the primary to the ticked row and clears the other row of the same kind', async () => {
    render(<Harness initial={[stored('a', 'email', 'a@x.it', true), stored('b', 'email', 'b@x.it')]} />)

    await userEvent.click(primaryBox(2))

    expect(primaryBox(1)).not.toBeChecked()
    expect(primaryBox(2)).toBeChecked()
  })

  it('does not touch the primary of another kind when one is ticked', async () => {
    render(
      <Harness
        initial={[
          stored('a', 'email', 'a@x.it', true),
          stored('b', 'email', 'b@x.it'),
          stored('p', 'phone', '0212345', true),
        ]}
      />,
    )

    await userEvent.click(primaryBox(2))

    expect(primaryBox(3)).toBeChecked()
  })

  it('hands the primary to the first remaining point of the kind when its holder is removed', async () => {
    render(
      <Harness
        initial={[
          stored('a', 'email', 'a@x.it', true),
          stored('b', 'email', 'b@x.it'),
          stored('c', 'email', 'c@x.it'),
        ]}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: 'Rimuovi recapito 1' }))

    expect(screen.getAllByRole('group')).toHaveLength(2)
    expect(primaryBox(1)).toBeChecked()
    expect(primaryBox(2)).not.toBeChecked()
    expect(latest.map((point) => point.value)).toEqual(['b@x.it', 'c@x.it'])
  })

  it('reorders with the move buttons, and disables them at the ends', async () => {
    render(<Harness initial={[stored('a', 'email', 'a@x.it', true), stored('p', 'phone', '0212345', true)]} />)

    expect(screen.getByRole('button', { name: 'Sposta su recapito 1' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Sposta giù recapito 2' })).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: 'Sposta su recapito 2' }))

    expect(latest.map((point) => point.kind)).toEqual(['phone', 'email'])
    expect(screen.getByLabelText('Valore recapito 1')).toHaveValue('0212345')
    expect(primaryBox(1)).toBeChecked()
    expect(primaryBox(2)).toBeChecked()
  })

  it('offers the labels in Italian and stores the English one', async () => {
    render(<Harness initial={[stored('a', 'email', 'a@x.it', true)]} />)

    // The closed select already shows the default.
    const trigger = screen.getByLabelText('Etichetta recapito 1')
    expect(trigger).toHaveTextContent('lavoro')

    await userEvent.click(trigger)
    const options = (await screen.findAllByRole('option')).map((option) => option.textContent)
    expect(options).toEqual(['lavoro', 'personale', 'amministrazione', 'altro'])

    await userEvent.click(screen.getByRole('option', { name: 'amministrazione' }))
    expect(latest[0]?.label).toBe('billing')
  })

  it('offers the three kinds, and re-flags the primaries when a row changes kind', async () => {
    render(<Harness initial={[stored('a', 'email', 'a@x.it', true), stored('b', 'email', 'b@x.it')]} />)

    await userEvent.click(screen.getByLabelText('Tipo recapito 1'))
    expect((await screen.findAllByRole('option')).map((option) => option.textContent)).toEqual([
      'Email',
      'Telefono',
      'LinkedIn',
    ])
    await userEvent.click(screen.getByRole('option', { name: 'Telefono' }))

    // The row left the emails: `b` is now the only one and so the primary; `a` is the first phone.
    expect(latest.map((point) => [point.kind, point.is_primary])).toEqual([
      ['phone', true],
      ['email', true],
    ])
  })

  it('limits a note to 200 characters', () => {
    render(<Harness initial={[stored('a', 'email', 'a@x.it', true)]} />)
    expect(screen.getByLabelText('Nota recapito 1')).toHaveAttribute('maxlength', '200')
  })

  it('shows the duplicate and bad-address sentences under the rows, only after a check', () => {
    const initial = [
      stored('a', 'email', 'ada@acme.it', true),
      stored('b', 'email', 'ADA@acme.it'),
      stored('c', 'email', 'non-una-email'),
    ]
    const { unmount } = render(<Harness initial={initial} />)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    unmount()

    render(<Harness initial={initial} checked />)
    expect(screen.getByRole('group', { name: 'Recapito 1' })).not.toHaveTextContent('già nell’elenco')
    expect(screen.getByRole('group', { name: 'Recapito 2' })).toHaveTextContent('già nell’elenco')
    expect(screen.getByRole('group', { name: 'Recapito 3' })).toHaveTextContent('Indirizzo email non valido')
    expect(screen.getByLabelText('Valore recapito 3')).toHaveAttribute('aria-invalid', 'true')
  })

  it('puts a server refusal on the row it names and the whole-list one under the list', () => {
    const refusal = (field: string, reason: string): ProblemDetail => ({
      type: 'about:blank',
      title: 'Dati non validi',
      status: 422,
      code: 'validation_failed',
      detail: `person.${field}: ${reason}`,
      field,
      reason,
    })
    const initial = [stored('a', 'email', 'a@x.it', true), stored('b', 'phone', '0212345', true)]

    const { unmount } = render(
      <Harness initial={initial} problem={refusal('contact_points[1]', 'numero di telefono non valido')} />,
    )
    expect(screen.getByRole('group', { name: 'Recapito 2' })).toHaveTextContent('numero di telefono non valido')
    expect(screen.getByRole('group', { name: 'Recapito 1' })).not.toHaveTextContent('non valido')
    unmount()

    render(<Harness initial={initial} problem={refusal('contact_points', 'al massimo 30 recapiti')} />)
    expect(screen.getByRole('alert')).toHaveTextContent('al massimo 30 recapiti')
  })

  it('leaves a blank row out of the request and sends no id, position or key', async () => {
    render(<Harness initial={[stored('a', 'email', 'a@x.it', true)]} />)
    await userEvent.click(screen.getByRole('button', { name: 'Aggiungi recapito' }))
    await userEvent.type(screen.getByLabelText('Nota recapito 2'), 'nessun valore')

    expect(latest).toHaveLength(2)
    expect(pointsToBody(latest)).toEqual([
      { kind: 'email', value: 'a@x.it', label: 'work', note: null, is_primary: true },
    ])
  })
})
