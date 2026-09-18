import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { FiscalPanel } from './FiscalPanel'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), PUT: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

function ok(data: unknown, status = 200) {
  return Promise.resolve({ data, response: new Response(null, { status }) } as never)
}

function failed(error: unknown, status: number) {
  return Promise.resolve({ error, response: new Response(null, { status }) } as never)
}

const RIFERIMENTO =
  "Operazione non soggetta a IVA ai sensi dell'art. 1, commi 54-89, L. 190/2014 - regime forfettario"

const PROFILE = {
  id: 'f-1',
  codice_regime: 'RF19',
  aliquota_iva_default: '0.00',
  natura_default: 'N2.2',
  riferimento_normativo: RIFERIMENTO,
  applica_bollo: true,
  soglia_bollo: '77.47',
  importo_bollo: '2.00',
  condizioni_pagamento: 'TP02',
  modalita_pagamento: 'MP05',
  // A number on the wire, unlike every other field here, which are strings.
  giorni_scadenza: 30,
  iban: null,
  coefficiente_redditivita: '67.00',
  aliquota_imposta_sostitutiva: '5.00',
  aliquota_inps: '26.07',
  created_at: '2026-08-20T09:00:00Z',
  updated_at: '2026-08-20T09:00:00Z',
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <FiscalPanel />
    </QueryClientProvider>,
  )
}

function bodyOfSave() {
  const call = vi.mocked(api.PUT).mock.calls[0] as unknown as [
    string,
    { body: Record<string, unknown> },
  ]
  return call[1].body
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.PUT).mockReset()
})

describe('FiscalPanel', () => {
  it('seeds the form from the stored profile', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(PROFILE))
    renderPanel()

    await waitFor(() => expect(screen.getByLabelText('Regime fiscale')).toHaveValue('RF19'))
    // The one numeric field on the wire still has to reach a text input as "30", not
    // as "[object Object]" and not as an empty box.
    expect(screen.getByLabelText('Giorni di scadenza')).toHaveValue('30')
    // A stored `null` becomes an empty input, never the string "null".
    expect(screen.getByLabelText('IBAN')).toHaveValue('')
    expect(screen.getByLabelText(/Applica il bollo/)).toBeChecked()
    expect(screen.queryByText(/Profilo non ancora configurato/)).not.toBeInTheDocument()
    // Rendered here so that the 422 test's "the hint gave way to the message" is a
    // real absence rather than a string that never matched anything.
    expect(screen.getByText('Obbligatoria quando l’aliquota è 0')).toBeInTheDocument()
  })

  /**
   * A fresh install has no row and `GET /api/fiscal-profile` answers 404. That is
   * "not configured yet", not a failure, and the form has to be usable right there --
   * without a fiscal profile no invoice can be issued at all, so a screen that only
   * said "missing" would be a dead end.
   */
  it('reads a 404 as not-yet-configured and still offers the form', async () => {
    vi.mocked(api.GET).mockImplementation(() => failed({ detail: 'not found' }, 404))
    renderPanel()

    expect(
      await screen.findByText(
        'Profilo non ancora configurato: senza di esso non è possibile emettere fatture.',
      ),
    ).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Salva' })).toBeEnabled()
  })

  /**
   * The blank form is pre-filled with the same values `FiscalProfileUpsert` defaults
   * to, and that is not decoration: the panel sends every key on every save, so the
   * server's own defaults never apply. If these drifted apart, a first save would
   * quietly store something nobody chose -- an RF19 forfettario with no natura is
   * refused by the SdI on the first invoice, not here.
   *
   * Every field on the form is pinned here, `riferimento_normativo` now included: it
   * was the one left unasserted, because the panel seeded it blank while the server
   * defaulted it to the forfettario sentence. That was the drift this test exists to
   * catch, so the panel was changed to offer the sentence rather than the test taught
   * to tolerate the gap.
   */
  it('offers the same defaults the server would have applied', async () => {
    vi.mocked(api.GET).mockImplementation(() => failed({ detail: 'not found' }, 404))
    renderPanel()

    await waitFor(() => expect(screen.getByLabelText('Regime fiscale')).toHaveValue('RF19'))
    expect(screen.getByLabelText('Aliquota IVA predefinita')).toHaveValue('0.00')
    expect(screen.getByLabelText('Natura')).toHaveValue('N2.2')
    expect(screen.getByLabelText('Riferimento normativo')).toHaveValue(RIFERIMENTO)
    expect(screen.getByLabelText('Soglia bollo')).toHaveValue('77.47')
    expect(screen.getByLabelText('Importo bollo')).toHaveValue('2.00')
    expect(screen.getByLabelText('Condizioni di pagamento')).toHaveValue('TP02')
    expect(screen.getByLabelText('Modalità di pagamento')).toHaveValue('MP05')
    expect(screen.getByLabelText('Giorni di scadenza')).toHaveValue('30')
    // The three income-calculation columns, `FiscalProfileUpsert`'s own defaults, as
    // percentages: `67.00`, not `0.67`.
    expect(screen.getByLabelText('Coefficiente di redditività')).toHaveValue('67.00')
    expect(screen.getByLabelText('Aliquota imposta sostitutiva')).toHaveValue('5.00')
    expect(screen.getByLabelText('Aliquota INPS')).toHaveValue('26.07')
    expect(screen.getByLabelText(/Applica il bollo/)).toBeChecked()
  })

  /**
   * The defect the three income fields were added to close, pinned as a test rather
   * than as a comment. `PUT /api/fiscal-profile` is a full replace, so a save writes
   * back every key the form holds -- and while the form did not know these three
   * existed, it held their defaults and nothing else. Editing an unrelated field on
   * this screen therefore reset a coefficient somebody had chosen, with no message and
   * nothing on the form to show what had gone.
   */
  it('sends the stored income parameters back, not the defaults', async () => {
    const custom = {
      ...PROFILE,
      coefficiente_redditivita: '78.00',
      aliquota_imposta_sostitutiva: '15.00',
      aliquota_inps: '24.48',
    }
    vi.mocked(api.GET).mockImplementation(() => ok(custom))
    vi.mocked(api.PUT).mockImplementation(() => ok(custom))
    renderPanel()

    await waitFor(() =>
      expect(screen.getByLabelText('Coefficiente di redditività')).toHaveValue('78.00'),
    )
    // An edit somewhere else entirely: the point is that a save the owner started for
    // another reason must not carry the defaults along with it.
    await userEvent.clear(screen.getByLabelText('Giorni di scadenza'))
    await userEvent.type(screen.getByLabelText('Giorni di scadenza'), '60')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    await waitFor(() => expect(api.PUT).toHaveBeenCalled())
    expect(bodyOfSave()).toMatchObject({
      coefficiente_redditivita: '78.00',
      aliquota_imposta_sostitutiva: '15.00',
      aliquota_inps: '24.48',
      giorni_scadenza: '60',
    })
  })

  /**
   * The alert, and *nothing else*. A 404 means "not configured yet" and gets the form
   * (the test above); anything else means the row may exist and simply could not be
   * read, and a blank form in that state invites somebody to fill it in and save -- a
   * PUT of every key, overwriting a profile the panel never managed to show them.
   */
  it('shows any other failure as an alert, and hides the form behind it', async () => {
    vi.mocked(api.GET).mockImplementation(() => failed({ detail: 'database non raggiungibile' }, 503))
    renderPanel()

    expect(await screen.findByRole('alert')).toHaveTextContent('database non raggiungibile')
    expect(screen.queryByRole('button', { name: 'Salva' })).not.toBeInTheDocument()
  })

  /**
   * The one thing a reader of this screen has to believe: an issued invoice carries its
   * own copy of these parameters, so editing them decides what the *next* document
   * looks like and never rewrites one already sent. The sentence is the only place the
   * product says so.
   */
  it('says that an issued invoice keeps its own copy of these values', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(PROFILE))
    renderPanel()

    expect(
      await screen.findByText(/cambiarli qui non\s+tocca i documenti già emessi/i),
    ).toBeInTheDocument()
  })

  /**
   * Clearing a field must send `''`, not omit the key: an omitted key would leave the
   * old value stored while the screen showed the field as empty -- the regression the
   * entity forms and `EmitterPanel` have both already had to fix.
   */
  it('sends an emptied field as an empty string rather than omitting it', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok({ ...PROFILE, iban: 'IT60X0542811101000000123456' }))
    vi.mocked(api.PUT).mockImplementation(() => ok(PROFILE))
    renderPanel()

    await waitFor(() =>
      expect(screen.getByLabelText('IBAN')).toHaveValue('IT60X0542811101000000123456'),
    )
    await userEvent.clear(screen.getByLabelText('IBAN'))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    await waitFor(() => expect(api.PUT).toHaveBeenCalled())
    expect(bodyOfSave()).toHaveProperty('iban', '')
  })

  /** `applica_bollo` is the one non-string field on the form, and `FiscalProfileUpsert`
   *  declares `extra="forbid"`: it has to arrive as a real `false`, never as `""` or
   *  `"off"`, and no key may be invented alongside it. */
  it('sends the bollo switch as a boolean, with no extra keys', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(PROFILE))
    vi.mocked(api.PUT).mockImplementation(() => ok({ ...PROFILE, applica_bollo: false }))
    renderPanel()

    await userEvent.click(await screen.findByLabelText(/Applica il bollo/))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    await waitFor(() => expect(api.PUT).toHaveBeenCalled())
    const body = bodyOfSave()
    expect(body).toHaveProperty('applica_bollo', false)
    expect(Object.keys(body).sort()).toEqual([
      'aliquota_imposta_sostitutiva',
      'aliquota_inps',
      'aliquota_iva_default',
      'applica_bollo',
      'codice_regime',
      'coefficiente_redditivita',
      'condizioni_pagamento',
      'giorni_scadenza',
      'iban',
      'importo_bollo',
      'modalita_pagamento',
      'natura_default',
      'riferimento_normativo',
      'soglia_bollo',
    ])
  })

  /**
   * `FiscalProfileService._check` refuses a zero default rate with no natura, naming
   * the field. That message belongs on the natura input: the screen has ten of them,
   * and a banner at the top never says which one to fix.
   */
  it('attaches a 422 that names a field to that field', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(PROFILE))
    const reason =
      "un'aliquota di default a zero richiede una natura, altrimenti ogni riepilogo prodotto verrebbe scartato dallo SdI"
    vi.mocked(api.PUT).mockImplementation(() =>
      failed(
        {
          type: 'https://pigrocrm.dev/errors/validation_failed',
          title: 'Dati non validi',
          status: 422,
          entity: 'fiscal_profile',
          field: 'natura_default',
          reason,
          expected: 'una natura, per esempio N2.2',
          detail: reason,
          code: 'validation_failed',
        },
        422,
      ),
    )
    renderPanel()

    await userEvent.clear(await screen.findByLabelText('Natura'))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(
      await screen.findByText(`${reason} (atteso: una natura, per esempio N2.2)`),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Natura')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByLabelText('Regime fiscale')).not.toHaveAttribute('aria-invalid')
    // The message is on the field, so the wide banner stays out of the way.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    // And the hint the message replaced is gone, rather than sitting under it
    // contradicting nothing but adding noise.
    expect(screen.queryByText('Obbligatoria quando l’aliquota è 0')).not.toBeInTheDocument()
  })

  /** A refusal with no field to blame -- a 403, a conflict -- has nowhere to go but the
   *  banner. Attributing it to nothing would leave the save silently doing nothing. */
  it('shows an unattributed refusal in the banner', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(PROFILE))
    vi.mocked(api.PUT).mockImplementation(() =>
      failed(
        {
          type: 'https://pigrocrm.dev/errors/permission_denied',
          title: 'Permesso negato',
          status: 403,
          detail: 'update_fiscal_profile requires one of [admin], actor has collaboratore',
          code: 'permission_denied',
        },
        403,
      ),
    )
    renderPanel()

    await userEvent.click(await screen.findByRole('button', { name: 'Salva' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/requires one of \[admin\]/)
  })

  /**
   * The form is keyed on the profile's identity, so it seeds at mount and a later
   * refetch cannot overwrite what is in the inputs. Seeding in an effect instead would
   * mean the invalidation fired by a successful save reaches back into the form and
   * replaces whatever the user has typed since.
   */
  it('does not reseed itself when the query refetches after a save', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(PROFILE))
    vi.mocked(api.PUT).mockImplementation(() => ok(PROFILE))
    renderPanel()

    await waitFor(() => expect(screen.getByLabelText('Regime fiscale')).toHaveValue('RF19'))
    await userEvent.clear(screen.getByLabelText('Regime fiscale'))
    await userEvent.type(screen.getByLabelText('Regime fiscale'), 'RF01')

    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalled())
    // The save invalidates the profile, so the panel asks again -- and the server here
    // still answers with the old document, `RF19` and all.
    await waitFor(() => expect(vi.mocked(api.GET).mock.calls.length).toBeGreaterThan(1))

    expect(screen.getByLabelText('Regime fiscale')).toHaveValue('RF01')
  })
})
