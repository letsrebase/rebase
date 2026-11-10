import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render as renderBare, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ModuleKey } from '@/lib/modules'
import { seedModules } from '@/lib/modules.testing'
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

/** The shortcut reads the space's modules (REB-953), so it is mounted in a query client;
 *  with `modulesOff` given they are known, otherwise every module is on. */
function render(ui: React.ReactElement, modulesOff?: readonly ModuleKey[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  if (modulesOff !== undefined) seedModules(client, modulesOff)
  return renderBare(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

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
        <div role="menu">
          <button role="menuitem">voce</button>
        </div>
      </>,
    )
    await user.click(screen.getByLabelText('campo'))
    await user.keyboard('n')
    await user.click(screen.getByRole('button', { name: 'dentro' }))
    await user.keyboard('n')
    await user.click(screen.getByRole('menuitem', { name: 'voce' }))
    await user.keyboard('n')
    await user.click(document.body)
    await user.keyboard('{Control>}n{/Control}')
    await user.keyboard('{Shift>}N{/Shift}')
    expect(navigate).not.toHaveBeenCalled()
  })

  it('does nothing with contracts off: there is no register to record the day in', async () => {
    const user = userEvent.setup()
    render(<RecordDayShortcut />, ['contracts'])
    await user.keyboard('n')
    expect(navigate).not.toHaveBeenCalled()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
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
