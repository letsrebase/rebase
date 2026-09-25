import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { formatDate, formatDateTime } from '@/lib/format'
import { MemberContratti } from './Contratti'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const QUADRO = {
  id: 'd1',
  kind: 'quadro',
  numero: null,
  stato: 'inviato',
  cliente: null,
  inizio: null,
  fine: null,
  sent_at: '2026-09-23T10:00:00Z',
  signed_at: null,
  signing_url: 'https://firma.letsrebase.com/sign/abc',
  ha_pdf_firmato: false,
  attivo: false,
  rinnovo: null,
  ultimo_giorno_disdetta: null,
}
const LETTERA = {
  ...QUADRO,
  id: 'd2',
  kind: 'lettera',
  numero: '2026-001',
  stato: 'in_attesa',
  cliente: 'ACME S.r.l.',
  inizio: '1° ottobre 2026',
  fine: '29 gennaio 2027',
  sent_at: null,
  signing_url: null,
}

function mount(contracts: unknown) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => answer(200, contracts))
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemberContratti />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('«Contratti» in the member area (REB-392)', () => {
  it('offers «Firma il documento» on what waits for a signature, and nothing on a letter that waits for its turn', async () => {
    mount({ quadro: QUADRO, quadri_precedenti: [], lettere: [LETTERA] })
    expect(await screen.findByRole('link', { name: 'Firma il contratto quadro' })).toHaveAttribute(
      'href',
      'https://firma.letsrebase.com/sign/abc',
    )
    expect(screen.getByText('Da firmare')).toBeInTheDocument()
    const letter = screen.getByText('Lettera di incarico n. 2026-001').closest('li')!
    expect(within(letter).getByText('Parte dopo la firma del contratto quadro')).toBeInTheDocument()
    expect(within(letter).getByText('ACME S.r.l., dal 1° ottobre 2026 al 29 gennaio 2027')).toBeInTheDocument()
    expect(within(letter).queryByRole('link')).toBeNull()
  })

  it('agrees in gender with the document: a letter is «Firmata», the framework agreement «Firmato»', async () => {
    mount({
      quadro: { ...QUADRO, stato: 'firmato', signing_url: null, ha_pdf_firmato: true },
      quadri_precedenti: [],
      lettere: [{ ...LETTERA, stato: 'firmato', signing_url: null, ha_pdf_firmato: true }],
    })
    expect(await screen.findByText('Firmato')).toBeInTheDocument()
    const letter = screen.getByText('Lettera di incarico n. 2026-001').closest('li')!
    expect(within(letter).getByText('Firmata')).toBeInTheDocument()
  })

  it('offers the signed copy and the framework agreement’s dates once signed, and no link to sign', async () => {
    mount({
      quadro: {
        ...QUADRO,
        stato: 'firmato',
        signing_url: null,
        signed_at: '2026-10-01T09:00:00Z',
        ha_pdf_firmato: true,
        attivo: true,
        rinnovo: '2027-10-01',
        ultimo_giorno_disdetta: '2027-09-01',
      },
      quadri_precedenti: [],
      lettere: [],
    })
    expect(
      await screen.findByRole('link', {
        name: `Scarica la copia firmata del contratto quadro del ${formatDateTime('2026-10-01T09:00:00Z')}`,
      }),
    ).toHaveAttribute('href', '/api/hub/me/contracts/d1/pdf')
    expect(screen.queryByRole('link', { name: /Firma/ })).toBeNull()
    expect(screen.getByText(new RegExp(formatDate('2027-10-01')))).toBeInTheDocument()
    expect(screen.getByText(/certificato della firma elettronica/)).toBeInTheDocument()
  })

  it('says a cancelled document is not to be signed, and offers no link', async () => {
    mount({
      quadro: { ...QUADRO, stato: 'annullato', signing_url: null },
      quadri_precedenti: [],
      lettere: [],
    })
    expect(await screen.findByText('Annullato: non va più firmato')).toBeInTheDocument()
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('says so, in words, when nothing has reached the person yet', async () => {
    mount({ quadro: null, quadri_precedenti: [], lettere: [] })
    expect(
      await screen.findByText('Nessun contratto per ora: quando rebase ti propone un incarico, lo trovi qui da firmare.'),
    ).toBeInTheDocument()
  })

  it('lists an earlier signed framework agreement under the current one, with its own copy', async () => {
    // Both signed the same day (REB-433): only the time tells the two links apart.
    const PREVIOUS = {
      ...QUADRO,
      id: 'd3',
      stato: 'disdetto',
      signing_url: null,
      signed_at: '2026-10-01T15:00:00Z',
      ha_pdf_firmato: true,
      attivo: false,
    }
    mount({
      quadro: {
        ...QUADRO,
        id: 'd4',
        stato: 'firmato',
        signing_url: null,
        signed_at: '2026-10-01T09:00:00Z',
        ha_pdf_firmato: true,
        attivo: true,
      },
      quadri_precedenti: [PREVIOUS],
      lettere: [],
    })

    expect(await screen.findByText('Contratti quadro precedenti')).toBeInTheDocument()
    // The current and the earlier framework agreement each offer «Copia firmata»: their
    // own signature date and time tell the two links apart by name (REB-433).
    const current = screen.getByRole('link', {
      name: `Scarica la copia firmata del contratto quadro del ${formatDateTime('2026-10-01T09:00:00Z')}`,
    })
    expect(current).toHaveAttribute('href', '/api/hub/me/contracts/d4/pdf')
    const previous = screen.getByRole('link', {
      name: `Scarica la copia firmata del contratto quadro del ${formatDateTime('2026-10-01T15:00:00Z')}`,
    })
    expect(previous).toHaveAttribute('href', '/api/hub/me/contracts/d3/pdf')
    expect(screen.queryByRole('link', { name: /Firma/ })).toBeNull()
  })

  it('shows nothing extra when there is no earlier signed framework agreement', async () => {
    mount({ quadro: QUADRO, quadri_precedenti: [], lettere: [] })
    await screen.findByText('Da firmare')
    expect(screen.queryByText('Contratti quadro precedenti')).toBeNull()
  })
})
