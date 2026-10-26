import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { api } from '@/lib/api'
import { AvvisiPage } from './AvvisiPage'
import { useUnreadCount } from './queries'
import {
  ACME_ACTIVITY_ID,
  CRITICAL,
  PROPOSAL_ID,
  ROSSI_DEAL_ID,
  SERIOUS,
  WARNING,
  fakeReminders,
  ok,
  reminder,
  renderWithQuery,
} from './testing'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn() } }
})

vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

/** The router is stood in for, as everywhere here: `Link` becomes an anchor with the href
 *  its `to`, `params` and `search` would produce, and `linkOptions` is the identity it is. */
vi.mock('@tanstack/react-router', () => ({
  linkOptions: (options: unknown) => options,
  Link: ({
    to,
    params,
    search,
    children,
    ...rest
  }: {
    to: string
    params?: Record<string, string>
    search?: Record<string, string>
    children: React.ReactNode
  }) => {
    const path = params ? to.replace(/\$(\w+)/g, (_, key: string) => params[key] ?? '') : to
    const query = search ? `?${new URLSearchParams(search).toString()}` : ''
    return (
      <a href={path + query} {...rest}>
        {children}
      </a>
    )
  },
}))

const GET = api.GET as unknown as Mock
const POST = api.POST as unknown as Mock

/** What the sidebar's badge reads, drawn next to the page so a test can watch both. */
function Count() {
  const unread = useUnreadCount()
  return <output aria-label="Da leggere">{unread ?? '-'}</output>
}

function page() {
  return renderWithQuery(
    <>
      <Count />
      <AvvisiPage />
    </>,
  )
}

function rowOf(title: string) {
  const row = screen.getByText(title).closest('li')
  if (!row) throw new Error(`no row for ${title}`)
  return within(row)
}

beforeEach(() => {
  GET.mockReset()
  POST.mockReset()
})

describe('the Avvisi page', () => {
  it('groups the open reminders by severity, severest first, each section with its count', async () => {
    fakeReminders([CRITICAL, SERIOUS, WARNING])
    page()

    const headings = await screen.findAllByRole('heading', { level: 2 })
    expect(headings.map((heading) => heading.textContent)).toEqual(['Urgenti1', 'Gravi1', 'Da guardare1'])
    const urgent = within(screen.getByRole('region', { name: /Urgenti/ }))
    expect(urgent.getByText('Mandare il preventivo ad Acme')).toBeInTheDocument()
    expect(urgent.queryByText('Rinnovo manutenzione Rossi')).not.toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: /Gravi/ })).getByText('Rinnovo manutenzione Rossi')).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: /Da guardare/ })).getByText('Timer acceso da 11 ore')).toBeInTheDocument()
  })

  it('shows the label, the title and the detail of each reminder', async () => {
    fakeReminders([CRITICAL])
    page()

    const row = await screen.findByText('Mandare il preventivo ad Acme')
    expect(row).toBeInTheDocument()
    expect(screen.getByText('Attività scaduta')).toBeInTheDocument()
    expect(screen.getByText('Scaduta da 16 giorni (il 10/10/2026).')).toBeInTheDocument()
    expect(screen.getByText('Urgente')).toBeInTheDocument()
    // The kind is the label above the row, so the title does not say it a second time.
    expect(screen.queryByText(/Attività scaduta: /)).not.toBeInTheDocument()
  })

  it('omits a severity with nothing in it', async () => {
    fakeReminders([SERIOUS])
    page()

    await screen.findByText('Rinnovo manutenzione Rossi')
    expect(screen.queryByRole('region', { name: /Urgenti/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: /Da guardare/ })).not.toBeInTheDocument()
  })

  it('opens the record each reminder is about', async () => {
    fakeReminders([CRITICAL, SERIOUS, WARNING])
    page()

    expect(await screen.findByRole('link', { name: 'Mandare il preventivo ad Acme' })).toHaveAttribute(
      'href',
      `/app/activities?apri=${ACME_ACTIVITY_ID}`,
    )
    expect(screen.getByRole('link', { name: 'Rinnovo manutenzione Rossi' })).toHaveAttribute(
      'href',
      `/app/deal/${ROSSI_DEAL_ID}`,
    )
    expect(screen.getByRole('link', { name: 'Timer acceso da 11 ore' })).toHaveAttribute('href', '/app/hours')
  })

  it('shows a reminder about an unknown kind of record, with no link', async () => {
    fakeReminders([
      reminder({
        key: 'contract_expiring:c1',
        label: 'Contratto in scadenza',
        title: 'Manutenzione Acme 2026',
        entity: 'contract',
        severity: 'warning',
      }),
    ])
    page()

    expect(await screen.findByText('Manutenzione Acme 2026')).toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    // It still carries the actions: a reminder is not useless for having no page.
    expect(screen.getByRole('button', { name: 'Visto' })).toBeInTheDocument()
  })

  it('marks a reminder seen: it leaves the list and the count, and waits under «Già gestiti»', async () => {
    const user = userEvent.setup()
    fakeReminders([CRITICAL, SERIOUS])
    page()
    await screen.findByText('Mandare il preventivo ad Acme')
    expect(screen.getByLabelText('Da leggere')).toHaveTextContent('2')

    await user.click(rowOf('Mandare il preventivo ad Acme').getByRole('button', { name: 'Visto' }))

    expect(POST).toHaveBeenCalledWith('/api/reminders/acknowledge', {
      body: { key: CRITICAL.key, severity: 'critical' },
    })
    await waitFor(() => expect(screen.queryByRole('region', { name: /Urgenti/ })).not.toBeInTheDocument())
    expect(screen.getByLabelText('Da leggere')).toHaveTextContent('1')

    // Collapsed until asked, then the row is there with what was done about it.
    expect(screen.queryByText('Mandare il preventivo ad Acme')).not.toBeInTheDocument()
    const toggle = screen.getByRole('button', { name: /Già gestiti/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    const handled = rowOf('Mandare il preventivo ad Acme')
    expect(handled.getByText('Visto')).toBeInTheDocument()
    expect(handled.queryByRole('button', { name: 'Visto' })).not.toBeInTheDocument()
  })

  it('offers the four days and no free date when postponing', async () => {
    const user = userEvent.setup()
    fakeReminders([CRITICAL])
    page()
    await screen.findByText('Mandare il preventivo ad Acme')

    await user.click(rowOf('Mandare il preventivo ad Acme').getByRole('button', { name: 'Rimanda' }))

    expect(screen.getAllByRole('menuitem').map((item) => item.textContent)).toEqual([
      'Domani',
      'Lunedì',
      'Tra una settimana',
      'Tra un mese',
    ])
  })

  /**
   * REB-734, «Done when»: «I snooze a reminder for a day and it disappears from the page
   * and the count until then.» The fake answers like the service: the reminder comes back
   * as `snoozed` until the day, so the read after the click confirms what the cache showed.
   */
  it('snoozes a reminder for a day and it disappears from the page and the count until then', async () => {
    const user = userEvent.setup()
    fakeReminders([CRITICAL, SERIOUS], '2026-10-27')
    page()
    await screen.findByText('Mandare il preventivo ad Acme')
    expect(screen.getByLabelText('Da leggere')).toHaveTextContent('2')

    await user.click(rowOf('Mandare il preventivo ad Acme').getByRole('button', { name: 'Rimanda' }))
    await user.click(screen.getByRole('menuitem', { name: 'Domani' }))

    expect(POST).toHaveBeenCalledWith('/api/reminders/snooze', {
      body: { key: CRITICAL.key, severity: 'critical', until: 'tomorrow' },
    })
    // Gone from the page and from the count...
    await waitFor(() => expect(screen.queryByText('Mandare il preventivo ad Acme')).not.toBeInTheDocument())
    expect(screen.getByLabelText('Da leggere')).toHaveTextContent('1')
    expect(screen.getByText('Rinnovo manutenzione Rossi')).toBeInTheDocument()
    // ...and the page kept the list the server answered, so it is out for the day and not
    // just hidden by the click: the read that followed still has it as snoozed.
    await waitFor(() => expect(GET.mock.calls.length).toBeGreaterThan(1))
    await user.click(screen.getByRole('button', { name: /Già gestiti/ }))
    expect(rowOf('Mandare il preventivo ad Acme').getByText('Rimandato fino al 27/10/2026')).toBeInTheDocument()
    expect(screen.getByLabelText('Da leggere')).toHaveTextContent('1')
  })

  it('brings a snoozed reminder back when the day has come and the server says open again', async () => {
    fakeReminders([{ ...CRITICAL, status: 'snoozed', snoozed_until: '2026-10-27' }])
    const client = page()
    await screen.findByText('Nessun avviso')
    expect(screen.getByLabelText('Da leggere')).toHaveTextContent('0')

    // The day comes: the next read of the list has it open again.
    GET.mockImplementation(() => Promise.resolve(ok([CRITICAL])))
    await client.invalidateQueries({ queryKey: ['reminders'] })

    expect(await screen.findByText('Mandare il preventivo ad Acme')).toBeInTheDocument()
    expect(screen.getByLabelText('Da leggere')).toHaveTextContent('1')
  })

  it('says so when there is nothing to look at, and keeps the handled ones out of the way', async () => {
    fakeReminders([{ ...CRITICAL, status: 'acknowledged' }])
    page()

    expect(await screen.findByRole('heading', { name: 'Nessun avviso' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Già gestiti/ })).toBeInTheDocument()
  })

  it('shows the empty state and no «Già gestiti» when the list is empty', async () => {
    fakeReminders([])
    page()

    expect(await screen.findByRole('heading', { name: 'Nessun avviso' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Già gestiti/ })).not.toBeInTheDocument()
  })

  it('names a failed read, and draws no count for it', async () => {
    GET.mockResolvedValue({
      error: { detail: 'Il database non risponde.', code: 'internal', title: 'x', status: 500, instance: '/' },
      response: new Response(null, { status: 500 }),
    })
    page()

    expect(await screen.findByText(/Il database non risponde/)).toBeInTheDocument()
    expect(screen.getByLabelText('Da leggere')).toHaveTextContent('-')
  })

  it('opens the proposal page for a reminder about a proposal', async () => {
    fakeReminders([
      reminder({
        key: `proposal_pending:${PROPOSAL_ID}`,
        label: 'Proposte in attesa',
        title: 'Una proposta aspetta da 8 giorni',
        entity: 'proposal',
        entity_id: PROPOSAL_ID,
        severity: 'serious',
      }),
    ])
    page()

    expect(await screen.findByRole('link', { name: 'Una proposta aspetta da 8 giorni' })).toHaveAttribute(
      'href',
      `/app/proposals/${PROPOSAL_ID}`,
    )
  })
})
