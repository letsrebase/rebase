import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemberReferral } from './Referral'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function mount(referral: unknown, status = 200) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => answer(status, referral))
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemberReferral />
    </QueryClientProvider>,
  )
}

const RATES = { rate_freelancer: '0.1000', rate_company: '0.3000' }

afterEach(() => vi.restoreAllMocks())

describe('«Il tuo link di segnalazione» in the member area (P-REB-44)', () => {
  it('shows the code as a link, and «Nessuna segnalazione ancora» with nothing referred', async () => {
    mount({ ...RATES, code: 'ABC123', referred: [] })
    const input = await screen.findByLabelText('Link di segnalazione da copiare')
    expect(input).toHaveValue(`${window.location.origin}/hub/?rif=ABC123`)
    expect(screen.getByText('Nessuna segnalazione ancora.')).toBeInTheDocument()
  })

  it('lists who the code brought in, freelance and company alike', async () => {
    mount({
      ...RATES,
      code: 'ABC123',
      referred: [
        { kind: 'freelancer', nome: 'Ada Lovelace', created_at: '2026-09-20T10:00:00Z', stato: 'nuovo' },
        { kind: 'company', nome: 'ACME S.r.l.', created_at: '2026-09-22T10:00:00Z', stato: 'nuovo' },
      ],
    })
    expect(await screen.findByText('Ada Lovelace')).toBeInTheDocument()
    expect(screen.getByText('Freelance')).toBeInTheDocument()
    expect(screen.getByText('ACME S.r.l.')).toBeInTheDocument()
    expect(screen.getByText('Azienda')).toBeInTheDocument()
  })

  it('copies the link to the clipboard on the button', async () => {
    // `userEvent.setup()` installs its own `navigator.clipboard` stub, which would
    // otherwise shadow this test's spy: define the spy after setup, not before.
    const user = userEvent.setup()
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    mount({ ...RATES, code: 'ABC123', referred: [] })
    const button = await screen.findByRole('button', { name: 'Copia link' })

    await user.click(button)

    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/hub/?rif=ABC123`)
    expect(await screen.findByRole('button', { name: 'Copiato' })).toBeInTheDocument()
  })

  it('shows a sentence instead of a blank section on a refusal', async () => {
    mount({ detail: 'nope' }, 500)
    expect(await screen.findByText('Non riesco a leggere il tuo link. Riprova tra poco.')).toBeInTheDocument()
  })
})

describe('what a referral earns, from the live rates (REB-610)', () => {
  it('states both rates under the title, in Italian percentages with no trailing zeros', async () => {
    mount({ rate_freelancer: '0.1250', rate_company: '0.3000', code: 'ABC123', referred: [] })
    expect(
      await screen.findByText(
        "Se segnali un freelance ricevi il 12,5% del margine di rebase, se segnali un'azienda il 30%.",
        { exact: false },
      ),
    ).toBeInTheDocument()
  })

  it("takes the article a rate's Italian reading needs, l'8% and not il 8%", async () => {
    mount({ rate_freelancer: '0.0800', rate_company: '0.1100', code: 'ABC123', referred: [] })
    expect(
      await screen.findByText(
        "Se segnali un freelance ricevi l'8% del margine di rebase, se segnali un'azienda l'11%.",
        { exact: false },
      ),
    ).toBeInTheDocument()
  })

  it('opens «Come si calcola?» on a click and closes it on Escape, the example worked from the rates', async () => {
    const user = userEvent.setup()
    mount({ rate_freelancer: '0.1250', rate_company: '0.3500', code: 'ABC123', referred: [] })
    const trigger = await screen.findByRole('button', { name: 'Come si calcola?' })
    expect(screen.queryByText('Esempio con numeri inventati')).toBeNull()

    await user.click(trigger)

    const popover = await screen.findByRole('dialog', { name: /Come si calcola/ })
    expect(within(popover).getByText('Esempio con numeri inventati')).toBeInTheDocument()
    // 500 - 400 over 20 days is 2.000 €; 12,5% of it is 250 €, 35% of it 700 €.
    expect(popover).toHaveTextContent(/Margine: \(500 - 400\) × 20 = 2\.000,00\s€/)
    expect(popover).toHaveTextContent(/Segnali un freelance: 12,5% di 2\.000,00\s€ = 250,00\s€/)
    expect(popover).toHaveTextContent(/Segnali un'azienda: 35% di 2\.000,00\s€ = 700,00\s€/)

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('opens from the keyboard alone', async () => {
    const user = userEvent.setup()
    mount({ ...RATES, code: 'ABC123', referred: [] })
    const trigger = await screen.findByRole('button', { name: 'Come si calcola?' })
    trigger.focus()

    await user.keyboard('{Enter}')

    expect(await screen.findByRole('dialog')).toHaveTextContent('Segnali un freelance: 10% di 2.000,00')
  })

  it('says nothing about earnings, and keeps the link, when the rates are unreadable', async () => {
    mount({ rate_freelancer: null, rate_company: 'boh', code: 'ABC123', referred: [] })
    expect(await screen.findByLabelText('Link di segnalazione da copiare')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Copia link' })).toBeInTheDocument()
    expect(screen.queryByText(/del margine di rebase/)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Come si calcola?' })).toBeNull()
  })

  it('states only the side whose rate is readable', async () => {
    mount({ rate_freelancer: null, rate_company: '0.3000', code: 'ABC123', referred: [] })
    expect(
      await screen.findByText("Se segnali un'azienda ricevi il 30% del margine di rebase.", { exact: false }),
    ).toBeInTheDocument()
    expect(screen.queryByText(/freelance ricevi/)).toBeNull()
  })
})

