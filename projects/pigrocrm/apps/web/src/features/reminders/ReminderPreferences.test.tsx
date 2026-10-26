import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { api } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { ReminderPreferences } from './ReminderPreferences'
import { useReminders, useUnreadCount } from './queries'
import { CRITICAL, ok, renderWithQuery } from './testing'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() } }
})

vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

const auth = vi.hoisted(() => ({ admin: true }))
vi.mock('@/lib/auth', () => ({ useIsAdmin: () => auth.admin }))

type Row = components['schemas']['ReminderPreferenceRead']
type Types = components['schemas']['ReminderTypeRead'][]
type Write = components['schemas']['ReminderPreferenceWrite']

const GET = api.GET as unknown as Mock
const PATCH = api.PATCH as unknown as Mock
const DELETE = api.DELETE as unknown as Mock

const TYPES: Types = [
  {
    key: 'attivita_due',
    label: 'Attività in scadenza',
    description: "Un'attività aperta scade oggi.",
    subject: 'attivita',
    audience: 'subject',
    direction: 'until',
    unit: 'days',
    ladder: { warning: 0 },
    daily: false,
  },
  {
    key: 'invoice_overdue',
    label: 'Fattura scaduta',
    description: 'Una fattura emessa ha superato la scadenza.',
    subject: 'invoice',
    audience: 'subject',
    direction: 'since',
    unit: 'days',
    ladder: { warning: 1, serious: 8, critical: 30 },
    daily: false,
  },
  {
    key: 'ceiling_approaching',
    label: 'Tetto dei ricavi vicino',
    description: 'I ricavi dell’anno si avvicinano al tetto.',
    subject: 'fiscal',
    audience: 'admins',
    direction: 'since',
    unit: 'percent',
    ladder: { warning: 80, serious: 90, critical: 100 },
    daily: false,
  },
]

const SWITCHABLE = ['push', 'slack', 'email']

/**
 * A server that resolves the two layers the way `ReminderPreferenceService` does and keeps
 * a row per person, so a test follows a write to what each person reads next. `as` says who
 * is asking; the space's rows are shared, a person's own are theirs.
 */
function fakeServer() {
  const state = { as: 'marta', space: [] as Row[], people: {} as Record<string, Row[]> }
  const mine = () => (state.people[state.as] ??= [])
  const layer = (scope: string) => (scope === 'space' ? state.space : mine())
  const blank = (scope: 'space' | 'me', type: string | null): Row => ({
    scope,
    type,
    enabled: null,
    channels: null,
    ladder: null,
    urgent_from: null,
    email_mode: null,
    quiet_hours: null,
    summary_hour: null,
    updated_at: '2026-10-26T09:00:00Z',
  })
  const pick = (type: string, column: keyof Row) =>
    [mine(), state.space].map((rows) => rows.find((row) => row.type === type)?.[column]).find((v) => v != null)

  GET.mockImplementation((path: string) => {
    if (path === '/api/reminders') return Promise.resolve(ok([CRITICAL]))
    return Promise.resolve(
      ok({
        types: TYPES,
        space: state.space,
        mine: mine(),
        effective: TYPES.map((kind) => {
          const spaceRow = state.space.find((row) => row.type === kind.key)
          const ownRow = mine().find((row) => row.type === kind.key)
          const channels = (pick(kind.key, 'channels') as string[] | undefined) ?? SWITCHABLE
          return {
            type: kind.key,
            enabled: spaceRow?.enabled !== false && ownRow?.enabled !== false,
            channels: ['in_app', ...SWITCHABLE.filter((channel) => channels.includes(channel))],
            ladder: pick(kind.key, 'ladder') ?? kind.ladder,
            urgent_from: pick(kind.key, 'urgent_from') ?? 'critical',
            email_mode: pick(kind.key, 'email_mode') ?? null,
          }
        }),
        general: {
          quiet_hours: { enabled: true, start: '20:00', end: '08:00', weekend: true },
          summary_hour: 8,
        },
      }),
    )
  })

  PATCH.mockImplementation(
    (_path: string, init: { params: { path: { scope: 'space' | 'me'; type_key: string } }; body: Write }) => {
      const { scope, type_key } = init.params.path
      const type = type_key === 'general' ? null : type_key
      const kind = TYPES.find((candidate) => candidate.key === type)
      const ladder = init.body.ladder
      if (ladder && kind?.direction === 'since' && (ladder.serious ?? Infinity) <= (ladder.warning ?? -1)) {
        return Promise.resolve({
          error: {
            type: 'about:blank',
            title: 'Valore non valido',
            status: 422,
            code: 'validation_failed',
            field: 'ladder',
            detail: 'x',
            reason: 'la soglia «serious» deve essere più alta di «warning»',
            expected: 'soglie in ordine di gravità',
          },
          response: new Response(null, { status: 422 }),
        })
      }
      const rows = layer(scope)
      let row = rows.find((candidate) => candidate.type === type)
      if (!row) rows.push((row = blank(scope, type)))
      Object.assign(row, init.body)
      return Promise.resolve(ok(row))
    },
  )
  DELETE.mockImplementation((_path: string, init: { params: { path: { scope: 'space' | 'me'; type_key: string } } }) => {
    const { scope, type_key } = init.params.path
    const type = type_key === 'general' ? null : type_key
    const rows = layer(scope)
    const at = rows.findIndex((row) => row.type === type)
    if (at >= 0) rows.splice(at, 1)
    return Promise.resolve(ok(undefined))
  })
  return state
}

/** What a person reads as their effective channels for a type, straight from the API. */
async function effectiveChannels(type: string): Promise<string[]> {
  const result = (await api.GET('/api/reminders/preferences' as never)) as unknown as {
    data: { effective: { type: string; channels: string[] }[] }
  }
  return result.data.effective.find((row) => row.type === type)?.channels ?? []
}

function rowOf(label: string) {
  const row = screen.getByText(label).closest('li')
  if (!row) throw new Error(`no row for ${label}`)
  return within(row)
}

beforeEach(() => {
  GET.mockReset()
  PATCH.mockReset()
  DELETE.mockReset()
  auth.admin = true
})

describe('the reminder settings', () => {
  it('lists every type in its section, each with its words and its thresholds in its own unit', async () => {
    fakeServer()
    renderWithQuery(<ReminderPreferences scope="space" />)

    await screen.findByText('Fattura scaduta')
    expect(screen.getByRole('heading', { name: 'Attività e deal' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Fatture e fatturazione' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Spazio' })).toBeInTheDocument()
    // A type that climbs as time passes reads «da N giorni», one that climbs as the date
    // nears reads «N giorni prima», and a ratio of a limit reads in percent.
    const invoice = rowOf('Fattura scaduta')
    expect(invoice.getByLabelText('Grave')).toHaveValue('8')
    expect(invoice.getAllByText('da')).toHaveLength(3)
    expect(invoice.getAllByText('giorni')).toHaveLength(3)
    expect(rowOf('Attività in scadenza').getAllByText('giorni prima')).toHaveLength(3)
    expect(rowOf('Tetto dei ricavi vicino').getAllByText('%')).toHaveLength(3)
  })

  it('shows in-app as always on, and the three channels a person may switch', async () => {
    fakeServer()
    renderWithQuery(<ReminderPreferences scope="space" />)
    await screen.findByText('Fattura scaduta')

    const invoice = rowOf('Fattura scaduta')
    expect(invoice.getByRole('checkbox', { name: 'App (sempre attiva)' })).toBeDisabled()
    expect(invoice.getByRole('checkbox', { name: 'App (sempre attiva)' })).toBeChecked()
    for (const name of ['Notifica push', 'Slack', 'Email']) {
      expect(invoice.getByRole('checkbox', { name })).toBeChecked()
    }
  })

  it('lists the space-wide types to an admin in their own settings, and not to a member', async () => {
    fakeServer()
    auth.admin = false
    renderWithQuery(<ReminderPreferences scope="me" />)
    await screen.findByText('Fattura scaduta')
    expect(screen.queryByText('Tetto dei ricavi vicino')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Spazio' })).not.toBeInTheDocument()
  })

  it('lists the space-wide types to an admin in their own settings', async () => {
    fakeServer()
    renderWithQuery(<ReminderPreferences scope="me" />)
    expect(await screen.findByText('Tetto dei ricavi vicino')).toBeInTheDocument()
    expect(screen.getByText('Arriva solo agli amministratori.')).toBeInTheDocument()
  })

  /**
   * REB-735, «Done when» at the contract level: an admin sets email off for overdue
   * invoices in the space's defaults, the same person turns it back on for themselves, and
   * the API's effective channels then have email for them and not for a colleague, while
   * the Avvisi list is read again and still holds the reminder.
   */
  it('lets an admin switch email off for the space and a person switch it back on for themselves only', async () => {
    const user = userEvent.setup()
    const server = fakeServer()
    const reminders = (
      <>
        <Count />
        <ReminderPreferences scope="space" />
      </>
    )
    renderWithQuery(reminders)
    await screen.findByText('Fattura scaduta')
    await waitFor(() => expect(screen.getByLabelText('Da leggere')).toHaveTextContent('1'))
    const listReads = () => GET.mock.calls.filter(([path]) => path === '/api/reminders').length
    const before = listReads()

    // The admin sets the space's default: email off.
    await user.click(rowOf('Fattura scaduta').getByRole('checkbox', { name: 'Email' }))
    expect(PATCH).toHaveBeenCalledWith('/api/reminders/preferences/{scope}/{type_key}', {
      params: { path: { scope: 'space', type_key: 'invoice_overdue' } },
      body: { channels: ['push', 'slack'] },
    })
    await waitFor(() => expect(rowOf('Fattura scaduta').getByRole('checkbox', { name: 'Email' })).not.toBeChecked())
    expect(await effectiveChannels('invoice_overdue')).toEqual(['in_app', 'push', 'slack'])
    // The write read the Avvisi list again, and the reminder is still there.
    await waitFor(() => expect(listReads()).toBeGreaterThan(before))
    expect(screen.getByLabelText('Da leggere')).toHaveTextContent('1')
    cleanup()

    // The same person, in their own settings: the space's value is shown as the space's,
    // and turning email back on writes their own row only.
    renderWithQuery(<ReminderPreferences scope="me" />)
    await screen.findByText('Fattura scaduta')
    const email = rowOf('Fattura scaduta').getByRole('checkbox', { name: 'Email' })
    expect(email).not.toBeChecked()
    expect(rowOf('Fattura scaduta').getAllByText('Valore dello spazio').length).toBeGreaterThan(0)
    await user.click(email)
    expect(PATCH).toHaveBeenLastCalledWith('/api/reminders/preferences/{scope}/{type_key}', {
      params: { path: { scope: 'me', type_key: 'invoice_overdue' } },
      body: { channels: ['push', 'slack', 'email'] },
    })
    await waitFor(() => expect(rowOf('Fattura scaduta').getByRole('checkbox', { name: 'Email' })).toBeChecked())

    // What the API reads back: email for her, not for a colleague; the space row is as it was.
    expect(await effectiveChannels('invoice_overdue')).toContain('email')
    server.as = 'luca'
    expect(await effectiveChannels('invoice_overdue')).not.toContain('email')
    expect(server.space.find((row) => row.type === 'invoice_overdue')?.channels).toEqual(['push', 'slack'])
  })

  it('disables the switch of a type the space turned off, and says why', async () => {
    const server = fakeServer()
    server.space.push({
      scope: 'space',
      type: 'invoice_overdue',
      enabled: false,
      channels: null,
      ladder: null,
      urgent_from: null,
      email_mode: null,
      quiet_hours: null,
      summary_hour: null,
      updated_at: '2026-10-26T09:00:00Z',
    })
    renderWithQuery(<ReminderPreferences scope="me" />)
    await screen.findByText('Fattura scaduta')

    const row = rowOf('Fattura scaduta')
    expect(row.getByRole('checkbox', { name: 'Fattura scaduta' })).toBeDisabled()
    expect(row.getByRole('checkbox', { name: 'Fattura scaduta' })).not.toBeChecked()
    expect(row.getByText('Lo spazio ha spento questo promemoria: non lo puoi riaccendere per te.')).toBeInTheDocument()
    expect(PATCH).not.toHaveBeenCalled()
  })

  it('goes back to the space value by deleting the override, never by writing the value into it', async () => {
    const user = userEvent.setup()
    const server = fakeServer()
    server.space.push({ ...rowFor('space', 'invoice_overdue'), channels: ['push'] })
    server.people.marta = [{ ...rowFor('me', 'invoice_overdue'), channels: ['push', 'email'] }]
    renderWithQuery(<ReminderPreferences scope="me" />)
    await screen.findByText('Fattura scaduta')
    expect(rowOf('Fattura scaduta').getAllByText('Tuo valore').length).toBeGreaterThan(0)

    await user.click(rowOf('Fattura scaduta').getByRole('button', { name: 'Torna al valore dello spazio' }))

    expect(DELETE).toHaveBeenCalledWith('/api/reminders/preferences/{scope}/{type_key}', {
      params: { path: { scope: 'me', type_key: 'invoice_overdue' } },
    })
    expect(PATCH).not.toHaveBeenCalled()
    await waitFor(() => expect(rowOf('Fattura scaduta').getByRole('checkbox', { name: 'Email' })).not.toBeChecked())
    expect(rowOf('Fattura scaduta').queryByRole('button', { name: 'Torna al valore dello spazio' })).not.toBeInTheDocument()
  })

  it("offers an admin «Torna al predefinito» on the space's own row only", async () => {
    const user = userEvent.setup()
    const server = fakeServer()
    server.space.push({ ...rowFor('space', 'invoice_overdue'), urgent_from: 'serious' })
    renderWithQuery(<ReminderPreferences scope="space" />)
    await screen.findByText('Fattura scaduta')

    expect(rowOf('Attività in scadenza').queryByRole('button', { name: 'Torna al predefinito' })).not.toBeInTheDocument()
    await user.click(rowOf('Fattura scaduta').getByRole('button', { name: 'Torna al predefinito' }))
    expect(DELETE).toHaveBeenCalledWith('/api/reminders/preferences/{scope}/{type_key}', {
      params: { path: { scope: 'space', type_key: 'invoice_overdue' } },
    })
  })

  it("shows the API's sentence under the thresholds when their order is refused, and keeps what was typed", async () => {
    const user = userEvent.setup()
    fakeServer()
    renderWithQuery(<ReminderPreferences scope="space" />)
    await screen.findByText('Fattura scaduta')

    const serious = rowOf('Fattura scaduta').getByLabelText('Grave')
    await user.clear(serious)
    await user.type(serious, '1')
    await user.click(rowOf('Fattura scaduta').getByRole('button', { name: 'Salva soglie' }))

    expect(PATCH).toHaveBeenCalledWith('/api/reminders/preferences/{scope}/{type_key}', {
      params: { path: { scope: 'space', type_key: 'invoice_overdue' } },
      body: { ladder: { warning: 1, serious: 1, critical: 30 } },
    })
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'la soglia «serious» deve essere più alta di «warning» (atteso: soglie in ordine di gravità)',
    )
    expect(rowOf('Fattura scaduta').getByLabelText('Grave')).toHaveValue('1')
  })

  it('saves a ladder with a severity left empty as a ladder without that rung', async () => {
    const user = userEvent.setup()
    fakeServer()
    renderWithQuery(<ReminderPreferences scope="space" />)
    await screen.findByText('Fattura scaduta')

    const save = rowOf('Fattura scaduta').getByRole('button', { name: 'Salva soglie' })
    expect(save).toBeDisabled()
    await user.clear(rowOf('Fattura scaduta').getByLabelText('Urgente'))
    await user.click(save)

    expect(PATCH).toHaveBeenCalledWith('/api/reminders/preferences/{scope}/{type_key}', {
      params: { path: { scope: 'space', type_key: 'invoice_overdue' } },
      body: { ladder: { warning: 1, serious: 8 } },
    })
  })

  it('refuses a threshold that is not a whole number before asking the API', async () => {
    const user = userEvent.setup()
    fakeServer()
    renderWithQuery(<ReminderPreferences scope="space" />)
    await screen.findByText('Fattura scaduta')

    const warning = rowOf('Fattura scaduta').getByLabelText('Da guardare')
    await user.clear(warning)
    await user.type(warning, '1,5')
    await user.click(rowOf('Fattura scaduta').getByRole('button', { name: 'Salva soglie' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Scrivi un numero intero, da 0 in su.')
    expect(PATCH).not.toHaveBeenCalled()
  })

  it('saves the urgency step and the email mode, and clears the mode with the neutral option', async () => {
    const user = userEvent.setup()
    fakeServer()
    renderWithQuery(<ReminderPreferences scope="space" />)
    await screen.findByText('Fattura scaduta')

    await user.click(rowOf('Fattura scaduta').getByRole('combobox', { name: 'Urgente da' }))
    await user.click(await screen.findByRole('option', { name: 'Grave' }))
    expect(PATCH).toHaveBeenLastCalledWith('/api/reminders/preferences/{scope}/{type_key}', {
      params: { path: { scope: 'space', type_key: 'invoice_overdue' } },
      body: { urgent_from: 'serious' },
    })

    await user.click(rowOf('Fattura scaduta').getByRole('combobox', { name: 'Invio email' }))
    await user.click(await screen.findByRole('option', { name: 'Nel riepilogo' }))
    expect(PATCH).toHaveBeenLastCalledWith('/api/reminders/preferences/{scope}/{type_key}', {
      params: { path: { scope: 'space', type_key: 'invoice_overdue' } },
      body: { email_mode: 'summary' },
    })

    await waitFor(() => expect(rowOf('Fattura scaduta').getByRole('combobox', { name: 'Invio email' })).toHaveTextContent('Nel riepilogo'))
    await user.click(rowOf('Fattura scaduta').getByRole('combobox', { name: 'Invio email' }))
    await user.click(await screen.findByRole('option', { name: "Secondo l'urgenza" }))
    expect(PATCH).toHaveBeenLastCalledWith('/api/reminders/preferences/{scope}/{type_key}', {
      params: { path: { scope: 'space', type_key: 'invoice_overdue' } },
      body: { email_mode: null },
    })
  })

  it('writes the whole quiet window when one part of it changes, and the summary hour on its own', async () => {
    const user = userEvent.setup()
    fakeServer()
    renderWithQuery(<ReminderPreferences scope="space" />)
    const general = within(await screen.findByRole('region', { name: 'Ore di silenzio e riepilogo' }))

    await user.click(general.getByRole('checkbox', { name: 'Anche sabato e domenica' }))
    expect(PATCH).toHaveBeenLastCalledWith('/api/reminders/preferences/{scope}/{type_key}', {
      params: { path: { scope: 'space', type_key: 'general' } },
      body: { quiet_hours: { enabled: true, start: '20:00', end: '08:00', weekend: false } },
    })

    await user.click(general.getByRole('combobox', { name: 'Ora del riepilogo' }))
    await user.click(await screen.findByRole('option', { name: '09:00' }))
    expect(PATCH).toHaveBeenLastCalledWith('/api/reminders/preferences/{scope}/{type_key}', {
      params: { path: { scope: 'space', type_key: 'general' } },
      body: { summary_hour: 9 },
    })
  })

  it('keeps what is typed in «Dalle» and «alle» and saves the whole window once, when the box loses focus', async () => {
    fakeServer()
    renderWithQuery(<ReminderPreferences scope="space" />)
    const general = within(await screen.findByRole('region', { name: 'Ore di silenzio e riepilogo' }))
    const from = general.getByLabelText('Dalle')
    const to = general.getByLabelText('alle')

    fireEvent.change(from, { target: { value: '21:30' } })
    fireEvent.change(to, { target: { value: '07:15' } })
    // Nothing is saved while the boxes are being edited, and what was typed stays.
    expect(PATCH).not.toHaveBeenCalled()
    expect(from).toHaveValue('21:30')

    fireEvent.blur(to)
    await waitFor(() => expect(PATCH).toHaveBeenCalledTimes(1))
    expect(PATCH).toHaveBeenCalledWith('/api/reminders/preferences/{scope}/{type_key}', {
      params: { path: { scope: 'space', type_key: 'general' } },
      body: { quiet_hours: { enabled: true, start: '21:30', end: '07:15', weekend: true } },
    })
    // After the refetch the boxes show what the server now holds, which is what was typed.
    await waitFor(() => expect(GET.mock.calls.filter(([path]) => path === '/api/reminders/preferences').length).toBeGreaterThan(1))
    expect(general.getByLabelText('Dalle')).toHaveValue('21:30')
    expect(general.getByLabelText('alle')).toHaveValue('07:15')
  })

  it('does not disable the time boxes while the save is in flight', async () => {
    fakeServer()
    renderWithQuery(<ReminderPreferences scope="space" />)
    const general = within(await screen.findByRole('region', { name: 'Ore di silenzio e riepilogo' }))
    // The TypeScript lib of this app predates `Promise.withResolvers`.
    let resolve: (value: unknown) => void = () => {}
    const promise = new Promise((done) => (resolve = done))
    PATCH.mockImplementationOnce(() => promise)

    fireEvent.change(general.getByLabelText('Dalle'), { target: { value: '22:00' } })
    fireEvent.blur(general.getByLabelText('Dalle'))
    await waitFor(() => expect(PATCH).toHaveBeenCalledTimes(1))

    expect(general.getByLabelText('Dalle')).toBeEnabled()
    expect(general.getByLabelText('alle')).toBeEnabled()
    expect(general.getByLabelText('Dalle')).toHaveValue('22:00')
    resolve(ok({}))
  })

  it('keeps what is typed in the other box while a save is in flight', async () => {
    fakeServer()
    renderWithQuery(<ReminderPreferences scope="space" />)
    const general = within(await screen.findByRole('region', { name: 'Ore di silenzio e riepilogo' }))
    let resolve: (value: unknown) => void = () => {}
    const promise = new Promise((done) => (resolve = done))
    PATCH.mockImplementationOnce(() => promise)

    fireEvent.change(general.getByLabelText('Dalle'), { target: { value: '22:00' } })
    fireEvent.blur(general.getByLabelText('Dalle'))
    await waitFor(() => expect(PATCH).toHaveBeenCalledTimes(1))
    // Typed after the first save started, before its answer.
    fireEvent.change(general.getByLabelText('alle'), { target: { value: '06:45' } })
    resolve(ok({}))

    await waitFor(() =>
      expect(GET.mock.calls.filter(([path]) => path === '/api/reminders/preferences').length).toBeGreaterThan(1),
    )
    expect(general.getByLabelText('alle')).toHaveValue('06:45')
  })

  it('puts a cleared box back to the saved time without asking the API', async () => {
    fakeServer()
    renderWithQuery(<ReminderPreferences scope="space" />)
    const general = within(await screen.findByRole('region', { name: 'Ore di silenzio e riepilogo' }))
    const from = general.getByLabelText('Dalle')

    fireEvent.change(from, { target: { value: '' } })
    fireEvent.blur(from)

    expect(PATCH).not.toHaveBeenCalled()
    expect(from).toHaveValue('20:00')
  })
})

/** A layer's empty row for a type, the shape the API returns. */
function rowFor(scope: 'space' | 'me', type: string): Row {
  return {
    scope,
    type,
    enabled: null,
    channels: null,
    ladder: null,
    urgent_from: null,
    email_mode: null,
    quiet_hours: null,
    summary_hour: null,
    updated_at: '2026-10-26T09:00:00Z',
  }
}

/** The sidebar's count and the page's list: both read the one Avvisi query. */
function Count() {
  const unread = useUnreadCount()
  useReminders()
  return <output aria-label="Da leggere">{unread ?? '-'}</output>
}
