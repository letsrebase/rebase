import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { RecordDayShortcut } from './RecordDayShortcut'

const navigate = vi.fn()
// The real `Link` navigates on click; the mock records the target the same way.
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children, ref }: { to: string; children: React.ReactNode; ref?: React.Ref<HTMLAnchorElement> }) => (
    <a
      ref={ref}
      href={to}
      onClick={(event) => {
        event.preventDefault()
        navigate({ to })
      }}
    >
      {children}
    </a>
  ),
}))
let canWrite = true
vi.mock('@/lib/auth', () => ({ useCanWrite: () => canWrite }))

beforeEach(() => {
  navigate.mockReset()
  canWrite = true
})

describe('the Home’s way into recording a day', () => {
  it('opens the form on the N key, from anywhere that is not a field', async () => {
    const user = userEvent.setup()
    render(<RecordDayShortcut />)
    await user.keyboard('n')
    expect(navigate).toHaveBeenCalledWith({ to: '/app/days/new' })
  })

  it('has the same way in as a button, with the key drawn on it', () => {
    render(<RecordDayShortcut />)
    expect(screen.getByRole('link', { name: /Registra una giornata/ })).toHaveAttribute('href', '/app/days/new')
  })

  it('leaves the key to a field, to a modifier and to a dialog', async () => {
    const user = userEvent.setup()
    render(
      <>
        <RecordDayShortcut />
        <input aria-label="campo" />
        <div role="dialog">
          <button>dentro</button>
        </div>
      </>,
    )
    await user.click(screen.getByLabelText('campo'))
    await user.keyboard('n')
    await user.click(screen.getByRole('button', { name: 'dentro' }))
    await user.keyboard('n')
    await user.click(document.body)
    await user.keyboard('{Control>}n{/Control}')
    await user.keyboard('{Shift>}N{/Shift}')
    expect(navigate).not.toHaveBeenCalled()
  })

  it('does nothing for a reader', async () => {
    canWrite = false
    const user = userEvent.setup()
    render(<RecordDayShortcut />)
    await user.keyboard('n')
    expect(navigate).not.toHaveBeenCalled()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })
})
