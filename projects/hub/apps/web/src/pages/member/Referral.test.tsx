import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
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

afterEach(() => vi.restoreAllMocks())

describe('«Il tuo link di segnalazione» in the member area (P-REB-44)', () => {
  it('shows the code as a link, and «Nessuna segnalazione ancora» with nothing referred', async () => {
    mount({ code: 'ABC123', referred: [] })
    const input = await screen.findByLabelText('Link di segnalazione da copiare')
    expect(input).toHaveValue(`${window.location.origin}/hub/?rif=ABC123`)
    expect(screen.getByText('Nessuna segnalazione ancora.')).toBeInTheDocument()
  })

  it('lists who the code brought in, freelance and company alike', async () => {
    mount({
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
    mount({ code: 'ABC123', referred: [] })
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
