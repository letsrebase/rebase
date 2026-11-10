import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { screensFromFields, Wizard, type Field } from './Wizard'

interface Form {
  first_name: string
  email: string
}

const FIELDS: Field<Form>[] = [
  {
    id: 'first_name',
    label: 'Come ti chiami?',
    render: ({ value, set, error, errorId }) => (
      <input
        aria-label="Nome"
        aria-invalid={!!error}
        aria-describedby={error ? errorId : undefined}
        value={value.first_name}
        onChange={(e) => set({ first_name: e.target.value })}
      />
    ),
    validate: (v) => (v.first_name.trim() ? null : 'Serve un nome.'),
    summary: (v) => v.first_name,
  },
  {
    id: 'email',
    label: 'La tua email?',
    render: ({ value, set, error, errorId }) => (
      <input
        aria-label="Email"
        aria-invalid={!!error}
        aria-describedby={error ? errorId : undefined}
        value={value.email}
        onChange={(e) => set({ email: e.target.value })}
      />
    ),
    validate: (v) => (v.email.includes('@') ? null : 'Serve una email.'),
    summary: (v) => v.email,
  },
]
const SCREENS = screensFromFields(FIELDS)

function Harness({
  onSubmit,
  submitError = null,
}: {
  onSubmit: () => void
  submitError?: { message: string; field?: string } | null
}) {
  const [value, setValue] = useState<Form>({ first_name: '', email: '' })
  return (
    <Wizard
      title="Test"
      screens={SCREENS}
      value={value}
      set={(patch) => setValue((v) => ({ ...v, ...patch }))}
      onSubmit={onSubmit}
      submitting={false}
      submitError={submitError}
      submitLabel="Invia"
    />
  )
}

describe('Wizard', () => {
  it('asks one question at a time, refuses to go on with an empty answer, reviews, submits', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<Harness onSubmit={onSubmit} />)

    expect(screen.getByRole('heading', { name: 'Come ti chiami?' })).toBeInTheDocument()
    expect(screen.getByText('1 di 2')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Avanti/ }))
    expect(screen.getByRole('alert')).toHaveTextContent('Serve un nome.')

    await user.type(screen.getByLabelText('Nome'), 'Ada{Enter}')
    expect(screen.getByRole('heading', { name: 'La tua email?' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    await user.type(screen.getByLabelText('Email'), 'ada@studio.it{Enter}')
    expect(screen.getByRole('heading', { name: 'Tutto giusto?' })).toBeInTheDocument()
    expect(screen.getByText('ada@studio.it')).toBeInTheDocument()

    await user.click(screen.getAllByRole('button', { name: 'Modifica' })[0]!)
    expect(screen.getByLabelText('Nome')).toHaveValue('Ada')
    await user.click(screen.getByRole('button', { name: /Avanti/ }))
    await user.click(screen.getByRole('button', { name: /Rivedi/ }))

    await user.click(screen.getByRole('button', { name: /Invia/ }))
    expect(onSubmit).toHaveBeenCalledTimes(1)
  })

  it('renders the review list as dt/dd rows only, the shape the definition-list rule requires', async () => {
    const user = userEvent.setup()
    const { container } = render(<Harness onSubmit={() => {}} />)

    await user.type(screen.getByLabelText('Nome'), 'Ada{Enter}')
    await user.type(screen.getByLabelText('Email'), 'ada@studio.it{Enter}')
    expect(screen.getByRole('heading', { name: 'Tutto giusto?' })).toBeInTheDocument()

    // axe's only-dlitems check flattens a role-less div directly under a dl and then
    // rejects any element child of that div that is not a dt or a dd (REB-96): the
    // "Modifica" button has to live inside the dd, not beside it, or this fails.
    const dl = container.querySelector('dl')
    expect(dl).not.toBeNull()
    for (const row of Array.from(dl!.children)) {
      for (const child of Array.from(row.children)) {
        expect(['DT', 'DD']).toContain(child.tagName)
      }
    }
  })

  it('carries exactly one level-one heading naming the wizard, on the screen and on the review', async () => {
    const user = userEvent.setup()
    render(<Harness onSubmit={() => {}} />)

    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    expect(screen.getByRole('heading', { level: 1, name: 'Test' })).toBeInTheDocument()

    await user.type(screen.getByLabelText('Nome'), 'Ada{Enter}')
    await user.type(screen.getByLabelText('Email'), 'ada@studio.it{Enter}')
    expect(screen.getByRole('heading', { name: 'Tutto giusto?' })).toBeInTheDocument()
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    expect(screen.getByRole('heading', { level: 1, name: 'Test' })).toBeInTheDocument()
  })

  it('goes back to the screen a server error names by field id', async () => {
    const user = userEvent.setup()
    const { rerender } = render(<Harness onSubmit={() => {}} />)
    await user.type(screen.getByLabelText('Nome'), 'Ada{Enter}')
    await user.type(screen.getByLabelText('Email'), 'ada@studio.it{Enter}')
    expect(screen.getByRole('heading', { name: 'Tutto giusto?' })).toBeInTheDocument()

    rerender(
      <Harness onSubmit={() => {}} submitError={{ message: 'Email già usata.', field: 'email' }} />,
    )
    expect(screen.getByRole('heading', { name: 'La tua email?' })).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Email già usata.')
  })

  it('wires aria-invalid and aria-describedby to the field that failed validation', async () => {
    const user = userEvent.setup()
    render(<Harness onSubmit={() => {}} />)

    expect(screen.getByLabelText('Nome')).toHaveAttribute('aria-invalid', 'false')
    await user.click(screen.getByRole('button', { name: /Avanti/ }))

    // The control remounts when a field turns invalid, so the element found before
    // the click is stale: re-query it.
    const firstName = screen.getByLabelText('Nome')
    expect(firstName).toHaveAttribute('aria-invalid', 'true')
    const alert = screen.getByRole('alert')
    expect(firstName.getAttribute('aria-describedby')).toBe(alert.id)

    await user.type(firstName, 'Ada{Enter}')
    expect(screen.getByRole('heading', { name: 'La tua email?' })).toBeInTheDocument()
  })

  // REB-243: a server error naming an id that matches no field must never be silent.
  it('falls back to the review’s own alert when the error names no field at all', async () => {
    const user = userEvent.setup()
    const { rerender } = render(<Harness onSubmit={() => {}} />)
    await user.type(screen.getByLabelText('Nome'), 'Ada{Enter}')
    await user.type(screen.getByLabelText('Email'), 'ada@studio.it{Enter}')
    expect(screen.getByRole('heading', { name: 'Tutto giusto?' })).toBeInTheDocument()

    rerender(
      <Harness
        onSubmit={() => {}}
        submitError={{ message: 'Cognome troppo lungo.', field: 'last_name' }}
      />,
    )
    // No field here is called `last_name`, so the engine cannot jump anywhere: the person
    // stays on the review and the message is never dropped -- it is exactly this
    // silence REB-243 is about.
    expect(screen.getByRole('heading', { name: 'Tutto giusto?' })).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Cognome troppo lungo.')
  })
})

/** The same two questions, mounted the way a page that remembers a draft mounts them. */
function Resumable({
  initialIndex,
  onIndexChange,
  intro,
}: {
  initialIndex?: number
  onIndexChange?: (index: number) => void
  intro?: React.ReactNode
}) {
  const [value, setValue] = useState<Form>({ first_name: 'Ada', email: '' })
  return (
    <Wizard
      title="Test"
      screens={SCREENS}
      value={value}
      set={(patch) => setValue((v) => ({ ...v, ...patch }))}
      onSubmit={() => {}}
      submitting={false}
      submitError={null}
      submitLabel="Invia"
      initialIndex={initialIndex}
      onIndexChange={onIndexChange}
      intro={intro}
    />
  )
}

describe('Wizard, resumed from a draft and introduced', () => {
  it('starts from the screen it is told to and reports every move', async () => {
    const user = userEvent.setup()
    const onIndexChange = vi.fn()
    render(<Resumable initialIndex={1} onIndexChange={onIndexChange} />)
    expect(screen.getByRole('heading', { name: 'La tua email?' })).toBeInTheDocument()
    expect(onIndexChange).toHaveBeenLastCalledWith(1)
    await user.click(screen.getByRole('button', { name: 'Indietro' }))
    expect(screen.getByRole('heading', { name: 'Come ti chiami?' })).toBeInTheDocument()
    expect(onIndexChange).toHaveBeenLastCalledWith(0)
  })

  it('lands on the review when the draft was already there, never past it', () => {
    render(<Resumable initialIndex={99} />)
    expect(screen.getByRole('heading', { name: 'Tutto giusto?' })).toBeInTheDocument()
  })

  it('shows the intro above the first question and nowhere else', async () => {
    const user = userEvent.setup()
    render(<Resumable intro={<p>Otto domande, tre minuti.</p>} />)
    expect(screen.getByText('Otto domande, tre minuti.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Avanti/ }))
    expect(screen.getByRole('heading', { name: 'La tua email?' })).toBeInTheDocument()
    expect(screen.queryByText('Otto domande, tre minuti.')).not.toBeInTheDocument()
  })
})
