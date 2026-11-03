import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { formatDate, formatDateTime } from '@/lib/format'
import { MemberContracts } from './Contracts'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const FRAMEWORK = {
  id: 'd1',
  kind: 'framework',
  regime: 'vat_registered',
  label: 'Contratto quadro',
  number: null,
  status: 'sent',
  customer: null,
  start: null,
  end: null,
  sent_at: '2026-09-23T10:00:00Z',
  signed_at: null,
  signing_url: 'https://firma.letsrebase.com/sign/abc',
  has_pdf_signed: false,
  active: false,
  renewal: null,
  last_notice_day: null,
  pigro_url: null,
}
const LETTER = {
  ...FRAMEWORK,
  id: 'd2',
  kind: 'letter',
  label: 'Lettera di incarico',
  number: '2026-001',
  status: 'pending',
  customer: 'ACME S.r.l.',
  start: '1° ottobre 2026',
  end: '29 gennaio 2027',
  sent_at: null,
  signing_url: null,
}

function mount(contracts: unknown) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => answer(200, contracts))
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemberContracts />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('«Contratti» in the member area (REB-392)', () => {
  it('offers «Firma il documento» on what waits for a signature, and nothing on a letter that waits for its turn', async () => {
    mount({ framework: FRAMEWORK, previous_frameworks: [], letters: [LETTER] })
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

  it('offers no signing link for an address that is not a web address', async () => {
    mount({ framework: { ...FRAMEWORK, signing_url: 'javascript:alert(1)' }, previous_frameworks: [], letters: [] })
    await screen.findByText('Da firmare')
    expect(screen.queryByRole('link', { name: /Firma/ })).toBeNull()
  })

  it('agrees in gender with the document: a letter is «Firmata», the framework agreement «Firmato»', async () => {
    mount({
      framework: { ...FRAMEWORK, status: 'signed', signing_url: null, has_pdf_signed: true },
      previous_frameworks: [],
      letters: [{ ...LETTER, status: 'signed', signing_url: null, has_pdf_signed: true }],
    })
    expect(await screen.findByText('Firmato')).toBeInTheDocument()
    const letter = screen.getByText('Lettera di incarico n. 2026-001').closest('li')!
    expect(within(letter).getByText('Firmata')).toBeInTheDocument()
  })

  it('offers the signed copy and the framework agreement’s dates once signed, and no link to sign', async () => {
    mount({
      framework: {
        ...FRAMEWORK,
        status: 'signed',
        signing_url: null,
        signed_at: '2026-10-01T09:00:00Z',
        has_pdf_signed: true,
        active: true,
        renewal: '2027-10-01',
        last_notice_day: '2027-09-01',
      },
      previous_frameworks: [],
      letters: [],
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
      framework: { ...FRAMEWORK, status: 'cancelled', signing_url: null },
      previous_frameworks: [],
      letters: [],
    })
    expect(await screen.findByText('Annullato: non va più firmato')).toBeInTheDocument()
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('says so, in words, when nothing has reached the person yet', async () => {
    mount({ framework: null, previous_frameworks: [], letters: [] })
    expect(
      await screen.findByText('Nessun contratto per ora: quando rebase ti propone un incarico, lo trovi qui da firmare.'),
    ).toBeInTheDocument()
  })

  it('lists an earlier signed framework agreement under the current one, with its own copy', async () => {
    // Both signed the same day (REB-433): only the time tells the two links apart.
    const PREVIOUS = {
      ...FRAMEWORK,
      id: 'd3',
      status: 'notice_served',
      signing_url: null,
      signed_at: '2026-10-01T15:00:00Z',
      has_pdf_signed: true,
      active: false,
    }
    mount({
      framework: {
        ...FRAMEWORK,
        id: 'd4',
        status: 'signed',
        signing_url: null,
        signed_at: '2026-10-01T09:00:00Z',
        has_pdf_signed: true,
        active: true,
      },
      previous_frameworks: [PREVIOUS],
      letters: [],
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
    mount({ framework: FRAMEWORK, previous_frameworks: [], letters: [] })
    await screen.findByText('Da firmare')
    expect(screen.queryByText('Contratti quadro precedenti')).toBeNull()
  })

  it('opens the letter’s deal on Pigro once the match is linked, and says what rebase reads (REB-503)', async () => {
    const deal = 'https://pigro.letsrebase.com/ada/app/deal/6f1c2d3e-0000-4000-8000-000000000009'
    const active = { ...LETTER, status: 'signed', signing_url: null, active: true, pigro_url: deal }
    mount({
      framework: { ...FRAMEWORK, status: 'signed', signing_url: null, active: true },
      previous_frameworks: [],
      letters: [active, { ...LETTER, id: 'd5', number: '2026-002' }],
    })

    const letter = (await screen.findByText('Lettera di incarico n. 2026-001')).closest('li')!
    const hours = within(letter).getByRole('link', { name: /^Le tue ore su Pigro/ })
    expect(hours).toHaveTextContent(/^Le tue ore su Pigro$/)
    expect(hours).toHaveAttribute('href', deal)
    expect(hours).toHaveAttribute('target', '_blank')
    expect(
      within(letter).getByText('rebase legge le ore di questo progetto per la rendicontazione al cliente.'),
    ).toBeInTheDocument()
    // A letter whose match is not linked says nothing about Pigro.
    const other = screen.getByText('Lettera di incarico n. 2026-002').closest('li')!
    expect(within(other).queryByRole('link', { name: /Pigro/ })).toBeNull()
    expect(within(other).queryByText(/rebase legge le ore/)).toBeNull()
    expect(screen.getAllByRole('link', { name: /Le tue ore su Pigro/ })).toHaveLength(1)
  })

  it('shows the occasional framework agreement beside the ordinary one, each with its own link (REB-687)', async () => {
    mount({
      framework: {
        ...FRAMEWORK,
        status: 'signed',
        signing_url: null,
        has_pdf_signed: true,
        active: true,
        signed_at: '2026-10-01T09:00:00Z',
        renewal: '2027-10-01',
        last_notice_day: '2027-09-01',
      },
      occasional_framework: {
        ...FRAMEWORK,
        id: 'd5',
        regime: 'occasional',
        label: 'Contratto quadro per prestazioni occasionali',
        signing_url: 'https://firma.letsrebase.com/sign/occ',
      },
      previous_frameworks: [],
      letters: [{ ...LETTER, regime: 'occasional', label: 'Lettera di incarico occasionale' }],
    })
    expect(await screen.findByText('Contratto quadro per prestazioni occasionali')).toBeInTheDocument()
    expect(screen.getByText('Contratto quadro')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Firma il contratto quadro per prestazioni occasionali' })).toHaveAttribute(
      'href',
      'https://firma.letsrebase.com/sign/occ',
    )
    expect(screen.queryByRole('link', { name: 'Firma il contratto quadro' })).toBeNull()
    expect(screen.getByText('Lettera di incarico occasionale n. 2026-001')).toBeInTheDocument()
    expect(screen.queryByText('Contratti quadro precedenti')).toBeNull()
  })
})
