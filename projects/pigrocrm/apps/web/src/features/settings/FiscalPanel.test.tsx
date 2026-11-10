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

const REFERENCE =
  "Operazione non soggetta a IVA ai sensi dell'art. 1, commi 54-89, L. 190/2014 - regime forfettario"

const PROFILE = {
  id: 'f-1',
  regime_code: 'RF19',
  default_vat_rate: '0.00',
  default_vat_nature: 'N2.2',
  legal_reference: REFERENCE,
  apply_stamp_duty: true,
  stamp_duty_threshold: '77.47',
  stamp_duty_amount: '2.00',
  payment_terms: 'TP02',
  payment_method: 'MP05',
  // A number on the wire, unlike every other field here, which are strings.
  due_days: 30,
  iban: null,
  profitability_coefficient: '67.00',
  substitute_tax_rate: '5.00',
  social_security_rate: '26.07',
  pack_id: 'it-flat-rate',
  pack_version: '1',
  created_at: '2026-08-20T09:00:00Z',
  updated_at: '2026-08-20T09:00:00Z',
}

const FOREIGN = {
  ...PROFILE,
  id: 'f-2',
  pack_id: 'non-it',
  regime_code: null,
  default_vat_rate: '20.00',
  default_vat_nature: null,
  legal_reference: null,
  apply_stamp_duty: false,
  profitability_coefficient: null,
  substitute_tax_rate: null,
  social_security_rate: null,
}

const ORDINARY = {
  ...PROFILE,
  id: 'f-3',
  pack_id: 'it-standard',
  regime_code: 'RF01',
  default_vat_rate: '22.00',
  default_vat_nature: null,
  legal_reference: null,
  apply_stamp_duty: false,
  profitability_coefficient: null,
  substitute_tax_rate: null,
  social_security_rate: null,
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <FiscalPanel legalEntityId="a-1" />
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

    await waitFor(() => expect(screen.getByLabelText('Regime')).toHaveValue('RF19'))
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
   * A fresh install has no row and `GET /api/aziende/{id}/fiscal-profile` answers 404. That is
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
   * quietly store something nobody chose -- an RF19 flat-rate regime with no natura is
   * refused by the SdI on the first invoice, not here.
   *
   * Every field on the form is pinned here, `legal_reference` now included: it
   * was the one left unasserted, because the panel seeded it blank while the server
   * defaulted it to the flat-rate regime sentence. That was the drift this test exists to
   * catch, so the panel was changed to offer the sentence rather than the test taught
   * to tolerate the gap.
   */
  it('offers the same defaults the server would have applied', async () => {
    vi.mocked(api.GET).mockImplementation(() => failed({ detail: 'not found' }, 404))
    renderPanel()

    await waitFor(() => expect(screen.getByLabelText('Regime')).toHaveValue('RF19'))
    expect(screen.getByLabelText('Aliquota IVA predefinita')).toHaveValue('0.00')
    expect(screen.getByLabelText('Natura')).toHaveValue('N2.2')
    expect(screen.getByLabelText('Riferimento normativo')).toHaveValue(REFERENCE)
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
   * than as a comment. `PUT /api/aziende/{id}/fiscal-profile` is a full replace, so a save writes
   * back every key the form holds -- and while the form did not know these three
   * existed, it held their defaults and nothing else. Editing an unrelated field on
   * this screen therefore reset a coefficient somebody had chosen, with no message and
   * nothing on the form to show what had gone.
   */
  it('sends the stored income parameters back, not the defaults', async () => {
    const custom = {
      ...PROFILE,
      profitability_coefficient: '78.00',
      substitute_tax_rate: '15.00',
      social_security_rate: '24.48',
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
      profitability_coefficient: '78.00',
      substitute_tax_rate: '15.00',
      social_security_rate: '24.48',
      due_days: '60',
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

  /** `apply_stamp_duty` is the one non-string field on the form, and `FiscalProfileUpsert`
   *  declares `extra="forbid"`: it has to arrive as a real `false`, never as `""` or
   *  `"off"`, and no key may be invented alongside it. */
  it('sends the bollo switch as a boolean, with no extra keys', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(PROFILE))
    vi.mocked(api.PUT).mockImplementation(() => ok({ ...PROFILE, apply_stamp_duty: false }))
    renderPanel()

    await userEvent.click(await screen.findByLabelText(/Applica il bollo/))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    await waitFor(() => expect(api.PUT).toHaveBeenCalled())
    const body = bodyOfSave()
    expect(body).toHaveProperty('apply_stamp_duty', false)
    expect(Object.keys(body).sort()).toEqual([
      'substitute_tax_rate',
      'social_security_rate',
      'default_vat_rate',
      'apply_stamp_duty',
      'regime_code',
      'profitability_coefficient',
      'payment_terms',
      'due_days',
      'iban',
      'stamp_duty_amount',
      'payment_method',
      'default_vat_nature',
      'pack_id',
      'legal_reference',
      'stamp_duty_threshold',
      // The version the form was seeded from (REB-622), beside the fifteen fields.
      'updated_at',
    ].sort())
    expect(body).toHaveProperty('pack_id', 'it-flat-rate')
    expect(body).toHaveProperty('regime_code', 'RF19')
  })

  it('declares a foreign azienda: no code, no bollo, the Italian fields gone and sent empty', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(PROFILE))
    vi.mocked(api.PUT).mockImplementation(() => ok(FOREIGN))
    renderPanel()

    await waitFor(() => expect(screen.getByLabelText('Regime')).toHaveValue('RF19'))
    await userEvent.selectOptions(screen.getByLabelText('Regime'), 'foreign')
    expect(screen.getByLabelText('Regime')).toHaveValue('foreign')
    // The fields an Italian regime reads leave the form; the ones a foreign company
    // still has stay, VAT nature and legal reference included, emptied of the flat-rate regime's.
    expect(screen.getByLabelText('Natura')).toHaveValue('')
    expect(screen.getByLabelText('Riferimento normativo')).toHaveValue('')
    expect(screen.queryByLabelText('Soglia bollo')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Coefficiente di redditività')).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/Applica il bollo/)).not.toBeInTheDocument()
    expect(screen.getByLabelText('Aliquota IVA predefinita')).toBeInTheDocument()
    expect(screen.getByLabelText('IBAN')).toBeInTheDocument()
    expect(screen.getByText(/aliquota del paese/)).toBeInTheDocument()

    await userEvent.clear(screen.getByLabelText('Aliquota IVA predefinita'))
    await userEvent.type(screen.getByLabelText('Aliquota IVA predefinita'), '20.00')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalled())
    const body = bodyOfSave()
    expect(body).toMatchObject({
      pack_id: 'non-it',
      regime_code: null,
      default_vat_rate: '20.00',
      default_vat_nature: null,
      legal_reference: null,
      apply_stamp_duty: false,
      profitability_coefficient: null,
      substitute_tax_rate: null,
      social_security_rate: null,
    })
    // The same fifteen keys as an Italian save, plus the version: a full replace leaves
    // nothing to the server's defaults, which are the forfettario's.
    expect(Object.keys(body)).toHaveLength(16)
  })

  it('reads a stored foreign profile back as «Estero» with the Italian fields hidden', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(FOREIGN))
    renderPanel()

    await waitFor(() => expect(screen.getByLabelText('Regime')).toHaveValue('foreign'))
    expect(screen.getByLabelText('Aliquota IVA predefinita')).toHaveValue('20.00')
    expect(screen.getByLabelText('Natura')).toHaveValue('')
    expect(screen.queryByLabelText('Soglia bollo')).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/Applica il bollo/)).not.toBeInTheDocument()
    // And back to the forfettario: the figures only it reads return as the server's own
    // defaults where the foreign profile has none (no coefficient, no declaration), with
    // the zero rate it alone allows, never a blank to be refused.
    await userEvent.selectOptions(screen.getByLabelText('Regime'), 'RF19')
    expect(screen.getByLabelText('Soglia bollo')).toHaveValue('77.47')
    expect(screen.getByLabelText('Coefficiente di redditività')).toHaveValue('67.00')
    expect(screen.getByLabelText('Aliquota IVA predefinita')).toHaveValue('0.00')
    expect(screen.getByLabelText('Natura')).toHaveValue('N2.2')
    expect(screen.getByLabelText(/Applica il bollo/)).toBeChecked()
  })

  it('shows a refusal that names a hidden field in the banner, never nowhere', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(FOREIGN))
    vi.mocked(api.PUT).mockImplementation(() =>
      failed(
        {
          code: 'validation_failed',
          detail: 'il bollo e’ attivo ma il suo importo non e’ positivo',
          entity: 'fiscal_profile',
          field: 'stamp_duty_amount',
        },
        422,
      ),
    )
    renderPanel()
    await waitFor(() => expect(screen.getByLabelText('Regime')).toHaveValue('foreign'))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/importo non e’ positivo/)
  })

  it('keeps the Italian values across an unsaved trip through «Estero»', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(PROFILE))
    renderPanel()

    await waitFor(() => expect(screen.getByLabelText('Regime')).toHaveValue('RF19'))
    await userEvent.selectOptions(screen.getByLabelText('Regime'), 'foreign')
    expect(screen.getByLabelText('Natura')).toHaveValue('')
    await userEvent.selectOptions(screen.getByLabelText('Regime'), 'RF19')
    expect(screen.getByLabelText('Natura')).toHaveValue('N2.2')
    expect(screen.getByLabelText('Riferimento normativo')).toHaveValue(REFERENCE)
    expect(screen.getByLabelText(/Applica il bollo/)).toBeChecked()
  })

  it('sends the Italian code the choice names', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(PROFILE))
    vi.mocked(api.PUT).mockImplementation(() => ok(ORDINARY))
    renderPanel()

    await waitFor(() => expect(screen.getByLabelText('Regime')).toHaveValue('RF19'))
    await userEvent.selectOptions(screen.getByLabelText('Regime'), 'RF01')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalled())
    // The ordinary regime is a pack of its own (REB-760), with the standard rate proposed
    // in place of the flat-rate regime's zero and nothing only the flat-rate regime carries.
    expect(bodyOfSave()).toMatchObject({
      pack_id: 'it-standard',
      regime_code: 'RF01',
      default_vat_rate: '22.00',
      default_vat_nature: null,
      legal_reference: null,
      apply_stamp_duty: false,
      profitability_coefficient: null,
      substitute_tax_rate: null,
      social_security_rate: null,
    })
  })

  it('saves an srl on the it-company pack, invoicing as RF01 with nothing of the forfettario', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(PROFILE))
    vi.mocked(api.PUT).mockImplementation(() => ok({ ...ORDINARY, pack_id: 'it-company' }))
    renderPanel()

    await waitFor(() => expect(screen.getByLabelText('Regime')).toHaveValue('RF19'))
    await userEvent.selectOptions(screen.getByLabelText('Regime'), 'company')
    expect(screen.getByLabelText('Aliquota IVA predefinita')).toHaveValue('22.00')
    expect(screen.queryByLabelText('Natura')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalled())
    expect(bodyOfSave()).toMatchObject({
      pack_id: 'it-company',
      regime_code: 'RF01',
      default_vat_rate: '22.00',
      default_vat_nature: null,
      apply_stamp_duty: false,
      profitability_coefficient: null,
    })
  })

  it('sends the default bollo amounts when they were blanked before leaving the forfettario', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(PROFILE))
    vi.mocked(api.PUT).mockImplementation(() => ok(ORDINARY))
    renderPanel()

    await waitFor(() => expect(screen.getByLabelText('Regime')).toHaveValue('RF19'))
    await userEvent.clear(screen.getByLabelText('Soglia bollo'))
    await userEvent.clear(screen.getByLabelText('Importo bollo'))
    await userEvent.selectOptions(screen.getByLabelText('Regime'), 'RF01')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalled())
    expect(bodyOfSave()).toMatchObject({ stamp_duty_threshold: '77.47', stamp_duty_amount: '2.00' })
  })

  it('shows an ordinary profile as «Ordinario» with only the fields it reads', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(ORDINARY))
    renderPanel()

    await waitFor(() => expect(screen.getByLabelText('Regime')).toHaveValue('RF01'))
    expect(screen.getByLabelText('Aliquota IVA predefinita')).toHaveValue('22.00')
    expect(screen.getByLabelText('IBAN')).toBeInTheDocument()
    // No VAT nature, no declaration, no stamp duty and none of the flat-rate regime's estimate.
    for (const label of [
      'Natura',
      'Riferimento normativo',
      'Soglia bollo',
      'Importo bollo',
      'Coefficiente di redditività',
      'Aliquota imposta sostitutiva',
      'Aliquota INPS',
    ]) {
      expect(screen.queryByLabelText(label)).not.toBeInTheDocument()
    }
    expect(screen.queryByLabelText(/Applica il bollo/)).not.toBeInTheDocument()
    expect(screen.getByText(/nessuna soglia del forfettario/)).toBeInTheDocument()
  })

  it('goes from the ordinary regime back to the forfettario with its own defaults', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(ORDINARY))
    renderPanel()

    await waitFor(() => expect(screen.getByLabelText('Regime')).toHaveValue('RF01'))
    await userEvent.selectOptions(screen.getByLabelText('Regime'), 'RF19')
    expect(screen.getByLabelText('Aliquota IVA predefinita')).toHaveValue('0.00')
    expect(screen.getByLabelText('Natura')).toHaveValue('N2.2')
    expect(screen.getByLabelText('Aliquota INPS')).toHaveValue('26.07')
    expect(screen.getByLabelText(/Applica il bollo/)).toBeChecked()
  })

  /**
   * `FiscalProfileService._check` refuses a zero default rate with no VAT nature, naming
   * the field. That message belongs on the VAT nature input: the screen has ten of them,
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
          field: 'default_vat_nature',
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
    expect(screen.getByLabelText('Regime')).not.toHaveAttribute('aria-invalid')
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
   * replaces whatever the user has typed since. The save's own answer is adopted (it is
   * the row as saved), and the refetch that follows brings the same version, so nothing
   * moves twice.
   */
  it('does not reseed itself when the query refetches after a save', async () => {
    const saved = { ...ORDINARY, updated_at: '2026-08-21T09:00:00Z' }
    vi.mocked(api.GET).mockImplementationOnce(() => ok(PROFILE)).mockImplementation(() => ok(saved))
    vi.mocked(api.PUT).mockImplementation(() => ok(saved))
    renderPanel()

    await waitFor(() => expect(screen.getByLabelText('Regime')).toHaveValue('RF19'))
    await userEvent.selectOptions(screen.getByLabelText('Regime'), 'RF01')

    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalled())
    // The save invalidates the profile, so the panel asks again.
    await waitFor(() => expect(vi.mocked(api.GET).mock.calls.length).toBeGreaterThan(1))

    expect(screen.getByLabelText('Regime')).toHaveValue('RF01')
  })

  // ---- the version check (REB-622, spec 2026-10-03 §11) ---------------------------------

  const STALE = {
    type: 'https://pigrocrm.dev/errors/stale_row',
    title: 'Riga cambiata nel frattempo',
    status: 409,
    detail: 'qualcun altro ha salvato nel frattempo: ricarica e riprova',
    code: 'stale_row',
    entity: 'fiscal_profile',
    updated_at: '2026-08-21T09:00:00Z',
  }

  it('locks the fields while a save is in flight, so nothing typed then is lost', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(PROFILE))
    let finish!: (value: unknown) => void
    vi.mocked(api.PUT).mockReturnValue(new Promise((resolve) => (finish = resolve)) as never)
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(screen.getByLabelText('IBAN')).toBeDisabled())
    expect(screen.getByLabelText('Regime')).toBeDisabled()
    expect(screen.getByLabelText(/Applica il bollo/)).toBeDisabled()
    finish({ data: PROFILE, response: new Response(null, { status: 200 }) })
    await waitFor(() => expect(screen.getByLabelText('IBAN')).toBeEnabled())
  })

  it('sends the version of the profile it was seeded from', async () => {
    vi.mocked(api.GET).mockImplementation(() => ok(PROFILE))
    vi.mocked(api.PUT).mockImplementation(() => ok(PROFILE))
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalled())
    expect(bodyOfSave()).toHaveProperty('updated_at', '2026-08-20T09:00:00Z')
  })

  it('sends null as the version on the first save of a profile', async () => {
    // A profile not saved yet was built on «nothing there», which the server accepts
    // while the row is still missing and refuses once somebody else created it.
    vi.mocked(api.GET).mockImplementation(() => failed({ detail: 'not found' }, 404))
    vi.mocked(api.PUT).mockImplementation(() => ok(PROFILE))
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalled())
    expect(bodyOfSave()).toHaveProperty('updated_at', null)
  })

  it('shows the saved values and carries the new version while the refetch is still out', async () => {
    const saved = { ...PROFILE, due_days: 60, updated_at: '2026-08-21T09:00:00Z' }
    let answer!: (value: unknown) => void
    vi.mocked(api.GET)
      .mockImplementationOnce(() => ok(PROFILE))
      .mockImplementation(() => new Promise((resolve) => (answer = resolve)) as never)
    vi.mocked(api.PUT).mockImplementation(() => ok(saved))
    renderPanel()
    await waitFor(() => expect(screen.getByLabelText('Regime')).toHaveValue('RF19'))
    await userEvent.clear(screen.getByLabelText('Giorni di scadenza'))
    await userEvent.type(screen.getByLabelText('Giorni di scadenza'), '60')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(1))
    // The refetch is held: the form shows what was saved, not the row before it.
    expect(screen.getByLabelText('Giorni di scadenza')).toHaveValue('60')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(2))
    const [, second] = vi.mocked(api.PUT).mock.calls[1] as unknown as [string, { body: Record<string, unknown> }]
    expect(second.body).toMatchObject({ due_days: '60', updated_at: '2026-08-21T09:00:00Z' })
    answer({ data: saved, response: new Response(null, { status: 200 }) })
  })

  it('follows a newer row while untouched, and keeps its seed under an open draft', async () => {
    const newer = { ...PROFILE, iban: 'IT60X0542811101000000123456', updated_at: '2026-08-21T09:00:00Z' }
    const newest = { ...newer, due_days: 45, updated_at: '2026-08-22T09:00:00Z' }
    vi.mocked(api.GET)
      .mockImplementationOnce(() => ok(PROFILE))
      .mockImplementationOnce(() => ok(newer))
      .mockImplementation(() => ok(newest))
    vi.mocked(api.PUT).mockImplementation(() => ok(newer))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <FiscalPanel legalEntityId="a-1" />
      </QueryClientProvider>,
    )
    await waitFor(() => expect(screen.getByLabelText('Regime')).toHaveValue('RF19'))
    // Untouched: another admin's row is adopted as it arrives.
    await client.invalidateQueries({ queryKey: ['fiscal-profile', 'a-1'] })
    await waitFor(() => expect(screen.getByLabelText('IBAN')).toHaveValue('IT60X0542811101000000123456'))
    // Touched: the next row is not, and the save carries the seed it was built on.
    await userEvent.clear(screen.getByLabelText('Giorni di scadenza'))
    await userEvent.type(screen.getByLabelText('Giorni di scadenza'), '60')
    await client.invalidateQueries({ queryKey: ['fiscal-profile', 'a-1'] })
    expect(screen.getByLabelText('Giorni di scadenza')).toHaveValue('60')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalled())
    expect(bodyOfSave()).toMatchObject({ due_days: '60', updated_at: '2026-08-21T09:00:00Z' })
  })

  it('keeps a first-save draft when another admin creates the profile, and reloads under it', async () => {
    // The form is keyed on the azienda alone: the profile appearing is a row that
    // arrived, not a remount that drops what was typed.
    const theirs = { ...PROFILE, iban: 'IT60X0542811101000000123456', updated_at: '2026-08-21T09:00:00Z' }
    vi.mocked(api.GET)
      .mockImplementationOnce(() => failed({ detail: 'not found' }, 404))
      .mockImplementation(() => ok(theirs))
    vi.mocked(api.PUT).mockImplementationOnce(() => failed({ ...STALE, updated_at: theirs.updated_at }, 409)).mockImplementation(() => ok(theirs))
    renderPanel()
    await userEvent.clear(await screen.findByLabelText('Giorni di scadenza'))
    await userEvent.type(screen.getByLabelText('Giorni di scadenza'), '60')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(bodyOfSave()).toHaveProperty('updated_at', null)
    await userEvent.click(await screen.findByRole('button', { name: 'Ricarica' }))
    await waitFor(() => expect(screen.getByLabelText('IBAN')).toHaveValue('IT60X0542811101000000123456'))
    expect(screen.getByLabelText('Giorni di scadenza')).toHaveValue('60')
    expect(screen.queryByText(/Profilo non ancora configurato/)).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(2))
    const [, second] = vi.mocked(api.PUT).mock.calls[1] as unknown as [string, { body: Record<string, unknown> }]
    expect(second.body).toMatchObject({ due_days: '60', updated_at: '2026-08-21T09:00:00Z' })
  })

  it('says a first-save draft could not be checked when the reload fails, and keeps it', async () => {
    vi.mocked(api.GET)
      .mockImplementationOnce(() => failed({ detail: 'not found' }, 404))
      .mockImplementation(() => failed({ detail: 'database non raggiungibile' }, 503))
    vi.mocked(api.PUT).mockImplementation(() => failed({ ...STALE, updated_at: '2026-08-21T09:00:00Z' }, 409))
    renderPanel()
    expect(await screen.findByText(/Profilo non ancora configurato/)).toBeInTheDocument()
    await userEvent.clear(screen.getByLabelText('Giorni di scadenza'))
    await userEvent.type(screen.getByLabelText('Giorni di scadenza'), '60')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Ricarica' }))
    // The absence seen earlier is no longer stated as a fact, the draft stays.
    await waitFor(() => expect(screen.getByText('database non raggiungibile')).toBeInTheDocument())
    expect(screen.getByText(/All’ultima lettura il profilo non era configurato/)).toBeInTheDocument()
    expect(screen.queryByText(/Profilo non ancora configurato:/)).not.toBeInTheDocument()
    expect(screen.getByLabelText('Giorni di scadenza')).toHaveValue('60')
    expect(screen.getByText(/Qualcun altro ha salvato nel frattempo/)).toBeInTheDocument()
  })

  it('keeps the draft and the refusal through a reload that fails', async () => {
    vi.mocked(api.GET)
      .mockImplementationOnce(() => ok(PROFILE))
      .mockImplementation(() => failed({ detail: 'database non raggiungibile' }, 503))
    vi.mocked(api.PUT).mockImplementation(() => failed(STALE, 409))
    renderPanel()
    await userEvent.clear(await screen.findByLabelText('Giorni di scadenza'))
    await userEvent.type(screen.getByLabelText('Giorni di scadenza'), '60')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Ricarica' }))
    // The failed read shows above the form; the form, the draft and the refusal stay.
    await waitFor(() => expect(screen.getByText('database non raggiungibile')).toBeInTheDocument())
    expect(screen.getByLabelText('Giorni di scadenza')).toHaveValue('60')
    expect(screen.getByText(/Qualcun altro ha salvato nel frattempo/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Ricarica' })).toBeEnabled()
  })

  it('on a stale refusal offers Ricarica, which takes the newer profile under the touched fields', async () => {
    const newer = { ...PROFILE, iban: 'IT60X0542811101000000123456', updated_at: '2026-08-21T09:00:00Z' }
    vi.mocked(api.GET).mockImplementationOnce(() => ok(PROFILE)).mockImplementation(() => ok(newer))
    vi.mocked(api.PUT).mockImplementationOnce(() => failed(STALE, 409)).mockImplementation(() => ok(newer))
    renderPanel()
    await waitFor(() => expect(screen.getByLabelText('Regime')).toHaveValue('RF19'))
    await userEvent.clear(screen.getByLabelText('Giorni di scadenza'))
    await userEvent.type(screen.getByLabelText('Giorni di scadenza'), '60')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Qualcun altro ha salvato nel frattempo.')
    expect(screen.getByLabelText('Giorni di scadenza')).toHaveValue('60')

    await userEvent.click(screen.getByRole('button', { name: 'Ricarica' }))
    await waitFor(() => expect(screen.getByLabelText('IBAN')).toHaveValue('IT60X0542811101000000123456'))
    expect(screen.getByLabelText('Giorni di scadenza')).toHaveValue('60')
    expect(screen.getByRole('alert')).toHaveTextContent('Riga ricaricata.')

    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(api.PUT).toHaveBeenCalledTimes(2))
    const [, second] = vi.mocked(api.PUT).mock.calls[1] as unknown as [string, { body: Record<string, unknown> }]
    expect(second.body).toMatchObject({
      due_days: '60',
      iban: 'IT60X0542811101000000123456',
      updated_at: '2026-08-21T09:00:00Z',
    })
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })
})
