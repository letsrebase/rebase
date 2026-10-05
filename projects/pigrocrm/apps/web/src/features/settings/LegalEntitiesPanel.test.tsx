/**
 * Impostazioni → Aziende with «Nuova azienda» (REB-632, spec 2026-10-03 §5): the one
 * button, the form it opens, the single request the form sends (the row and its fiscal
 * profile together), and the page landing on the new azienda once the list has two.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { LegalEntitiesPanel } from './LegalEntitiesPanel'
import type { LegalEntityRecord } from './queries'
import { api } from '@/lib/api'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), DELETE: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

function failed(error: unknown, status: number) {
  return { error, response: new Response(null, { status }) } as never
}

function azienda(id: string, nome: string, overrides: Partial<LegalEntityRecord> = {}): LegalEntityRecord {
  return {
    id,
    nome,
    predefinita: false,
    attiva: true,
    ragione_sociale: `${nome} S.r.l.`,
    partita_iva: null,
    codice_fiscale: null,
    indirizzo: null,
    cap: null,
    comune: null,
    provincia: null,
    nazione: 'IT',
    pec: null,
    codice_sdi: null,
    telefono: null,
    email: null,
    sito_web: null,
    logo_key: null,
    firma_key: null,
    firma_email: null,
    regime_fiscale: null,
    created_at: '2026-08-20T09:00:00Z',
    updated_at: '2026-08-20T09:00:00Z',
    ...overrides,
  }
}

const STUDIO = azienda('a-1', 'Studio Rossi', { predefinita: true })
const LTD = azienda('a-2', 'rebase ltd', { nazione: 'GB', ragione_sociale: 'Rebase Ltd' })

/** The list the page reads, as the server would answer it after each write. */
let listed: LegalEntityRecord[]

function mockReads() {
  vi.mocked(api.GET).mockImplementation((path: string) => {
    if (path === '/api/aziende') return Promise.resolve(ok(listed))
    // No fiscal profile yet on any azienda: the panel shows its empty form.
    return Promise.resolve(failed({ code: 'not_found' }, 404))
  })
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <LegalEntitiesPanel />
    </QueryClientProvider>,
  )
}

async function openTheForm() {
  const user = userEvent.setup()
  renderPanel()
  await user.click(await screen.findByRole('button', { name: 'Nuova azienda' }))
  const form = screen.getByRole('form', { name: 'Nuova azienda' })
  return { user, form }
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  listed = [STUDIO]
  mockReads()
})

describe('LegalEntitiesPanel, «Nuova azienda»', () => {
  it('offers the button even with one azienda, where the picker is not drawn', async () => {
    renderPanel()
    expect(await screen.findByRole('button', { name: 'Nuova azienda' })).toBeInTheDocument()
    expect(screen.queryByRole('tablist', { name: 'Aziende' })).not.toBeInTheDocument()
  })

  it('sends the row and its fiscal profile in one request and lands on the new azienda', async () => {
    const { user, form } = await openTheForm()
    vi.mocked(api.POST).mockImplementation(() => {
      listed = [STUDIO, LTD]
      return Promise.resolve(ok(LTD))
    })

    await user.type(within(form).getByLabelText('Nome breve *'), 'rebase ltd')
    await user.type(within(form).getByLabelText('Ragione sociale *'), 'Rebase Ltd')
    await user.clear(within(form).getByLabelText('Nazione'))
    await user.type(within(form).getByLabelText('Nazione'), 'gb')
    await user.selectOptions(within(form).getByLabelText('Regime'), 'estero')
    // Abroad the rate is the country's own and nobody's guess: blank, and required.
    expect(within(form).getByLabelText('Aliquota IVA predefinita')).toHaveValue('')
    expect(within(form).getByRole('button', { name: 'Crea azienda' })).toBeDisabled()
    await user.type(within(form).getByLabelText('Aliquota IVA predefinita'), '20.00')
    await user.type(within(form).getByLabelText('IBAN'), 'GB29NWBK60161331926819')
    await user.click(within(form).getByRole('button', { name: 'Crea azienda' }))

    await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1))
    const [path, init] = vi.mocked(api.POST).mock.calls[0] as unknown as [
      string,
      { body: Record<string, unknown> },
    ]
    expect(path).toBe('/api/aziende')
    expect(init.body).toMatchObject({
      nome: 'rebase ltd',
      ragione_sociale: 'Rebase Ltd',
      nazione: 'GB',
      partita_iva: null,
      fiscal_profile: {
        pack_id: 'non-it',
        codice_regime: null,
        aliquota_iva_default: '20.00',
        natura_default: null,
        applica_bollo: false,
        coefficiente_redditivita: null,
        iban: 'GB29NWBK60161331926819',
      },
    })

    // The page lands on the new azienda's own panels, with the picker now drawn.
    const tabs = await screen.findByRole('tablist', { name: 'Aziende' })
    expect(within(tabs).getByRole('tab', { name: /rebase ltd/ })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    expect(screen.queryByRole('form', { name: 'Nuova azienda' })).not.toBeInTheDocument()
    // One identity form, the new azienda's: the previous one is gone with the switch.
    await waitFor(() => expect(screen.getByLabelText('Ragione sociale *')).toHaveValue('Rebase Ltd'))
    expect(screen.getAllByLabelText('Nome breve')).toHaveLength(1)
  })

  it('switching the picker replaces the identity form instead of stacking a second one', async () => {
    listed = [STUDIO, LTD]
    const user = userEvent.setup()
    renderPanel()
    const tabs = await screen.findByRole('tablist', { name: 'Aziende' })
    expect(screen.getByLabelText('Ragione sociale *')).toHaveValue('Studio Rossi S.r.l.')
    await user.click(within(tabs).getByRole('tab', { name: 'rebase ltd' }))
    await waitFor(() => expect(screen.getByLabelText('Ragione sociale *')).toHaveValue('Rebase Ltd'))
    expect(screen.getAllByLabelText('Nome breve')).toHaveLength(1)
  })

  it('keeps the forfettario defaults for an Italian azienda', async () => {
    const { user, form } = await openTheForm()
    vi.mocked(api.POST).mockResolvedValue(ok(azienda('a-3', 'srl')))
    await user.type(within(form).getByLabelText('Nome breve *'), 'srl')
    await user.type(within(form).getByLabelText('Ragione sociale *'), 'Rossi S.r.l.')
    await user.selectOptions(within(form).getByLabelText('Regime'), 'RF01')
    expect(within(form).getByLabelText('Aliquota IVA predefinita')).toHaveValue('22.00')
    await user.click(within(form).getByRole('button', { name: 'Crea azienda' }))
    await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1))
    const [, init] = vi.mocked(api.POST).mock.calls[0] as unknown as [
      string,
      { body: { fiscal_profile: Record<string, unknown>; nazione: string } },
    ]
    // The ordinary regime at the standard rate carries no natura (the SdI discards one
    // beside a non-zero rate) and none of the forfettario's three income parameters,
    // which the server would otherwise default and the estimate would compute from.
    expect(init.body.fiscal_profile).toEqual({
      pack_id: 'it-flat-rate',
      codice_regime: 'RF01',
      aliquota_iva_default: '22.00',
      natura_default: null,
      riferimento_normativo: null,
      coefficiente_redditivita: null,
      aliquota_imposta_sostitutiva: null,
      aliquota_inps: null,
      iban: null,
    })
    expect(init.body.nazione).toBe('IT')
  })

  it('lets a zero rate carry the natura the server requires beside it', async () => {
    const { user, form } = await openTheForm()
    vi.mocked(api.POST).mockResolvedValue(ok(azienda('a-4', 'zero')))
    await user.type(within(form).getByLabelText('Nome breve *'), 'zero')
    await user.type(within(form).getByLabelText('Ragione sociale *'), 'Zero S.r.l.')
    await user.selectOptions(within(form).getByLabelText('Regime'), 'RF01')
    // Picking the regime proposes its rate and empties the natura; both stay editable.
    expect(within(form).getByLabelText('Natura')).toHaveValue('')
    await user.clear(within(form).getByLabelText('Aliquota IVA predefinita'))
    await user.type(within(form).getByLabelText('Aliquota IVA predefinita'), '0.00')
    await user.type(within(form).getByLabelText('Natura'), 'N3.5')
    await user.click(within(form).getByRole('button', { name: 'Crea azienda' }))
    await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1))
    const [, init] = vi.mocked(api.POST).mock.calls[0] as unknown as [
      string,
      { body: { fiscal_profile: Record<string, unknown> } },
    ]
    expect(init.body.fiscal_profile).toMatchObject({
      codice_regime: 'RF01',
      aliquota_iva_default: '0.00',
      natura_default: 'N3.5',
    })
  })

  it('puts a refusal under the field it names and keeps the form open', async () => {
    const { user, form } = await openTheForm()
    vi.mocked(api.POST).mockResolvedValue(
      failed(
        {
          type: 'about:blank',
          title: 'Dato non valido',
          status: 422,
          detail: 'emitter_profile.partita_iva: deve essere di 11 cifre',
          code: 'validation_failed',
          entity: 'emitter_profile',
          field: 'partita_iva',
          reason: 'deve essere di 11 cifre',
          expected: '11 cifre numeriche',
        },
        422,
      ),
    )
    await user.type(within(form).getByLabelText('Nome breve *'), 'srl')
    await user.type(within(form).getByLabelText('Ragione sociale *'), 'Rossi S.r.l.')
    await user.type(within(form).getByLabelText('Partita IVA'), '123')
    await user.click(within(form).getByRole('button', { name: 'Crea azienda' }))
    expect(
      await within(form).findByText('deve essere di 11 cifre (atteso: 11 cifre numeriche)'),
    ).toBeInTheDocument()
    expect(within(form).getByLabelText('Partita IVA')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.queryByRole('tablist', { name: 'Aziende' })).not.toBeInTheDocument()
  })

  it('cannot be submitted without the two required names, and Annulla goes back', async () => {
    const { user, form } = await openTheForm()
    expect(within(form).getByRole('button', { name: 'Crea azienda' })).toBeDisabled()
    await user.click(within(form).getByRole('button', { name: 'Annulla' }))
    expect(screen.queryByRole('form', { name: 'Nuova azienda' })).not.toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Nuova azienda' })).toBeInTheDocument()
    expect(api.POST).not.toHaveBeenCalled()
  })
})
