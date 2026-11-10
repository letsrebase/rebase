import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { viewport } from '@/features/inbox/testing'
import { EngagementsTable } from './EngagementsTable'
import { engagement } from './listFixtures'

const navigate = vi.fn()

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    to,
    params,
    children,
    ...rest
  }: {
    to: string
    params?: Record<string, string>
    children: React.ReactNode
    className?: string
    onClick?: (event: React.MouseEvent) => void
  }) => (
    <a href={params ? to.replace(/\$(\w+)/g, (_, key: string) => params[key] ?? '') : to} {...rest}>
      {children}
    </a>
  ),
  useNavigate: () => navigate,
}))

beforeEach(() => {
  navigate.mockReset()
  viewport(1280)
})

describe('EngagementsTable on a desk', () => {
  it('draws the four columns of the brief with the freelancer over «ruolo · cliente»', () => {
    render(<EngagementsTable engagements={[engagement()]} />)
    const table = screen.getByRole('table')
    for (const name of ['Freelance e incarico', 'Lettera', 'Compenso', 'Stato']) {
      expect(within(table).getByRole('columnheader', { name })).toBeInTheDocument()
    }
    expect(screen.getByText('Giulia Martini')).toBeInTheDocument()
    expect(screen.getByText('Backend developer · Alfa Logistica S.r.l.')).toBeInTheDocument()
    expect(screen.getByText('dal 02/11/2026, senza data di fine')).toBeInTheDocument()
    expect(screen.getByText(/450,00.*al giorno · 4 giorni a settimana/)).toBeInTheDocument()
  })

  it('says what a row waits for in a sentence, under the pill', () => {
    render(
      <EngagementsTable
        engagements={[
          engagement({
            state: 'active',
            letter_number: '2026-014',
            waiting: [{ code: 'report_undecided', month: '2026-10-01', tacit_on: '2026-11-12' }],
          }),
        ]}
      />,
    )
    expect(screen.getByText('Attivo')).toBeInTheDocument()
    expect(screen.getByText('2026-014')).toBeInTheDocument()
    expect(
      screen.getByText('Rendiconto di ottobre ricevuto: nessuna obiezione entro il 12/11/2026'),
    ).toBeInTheDocument()
  })

  it('says how it stands when nothing waits, and how a closed one ended', () => {
    render(
      <EngagementsTable
        engagements={[
          engagement({ id: 'e2', state: 'active', waiting: [] }),
          engagement({ id: 'e3', state: 'concluded', ends_on: '2026-10-30', waiting: [] }),
        ]}
      />,
    )
    expect(screen.getByText('In corso')).toBeInTheDocument()
    expect(screen.getByText('Concluso il 30/10/2026')).toBeInTheDocument()
  })

  it('names a missing field and a missing client as what the row waits for', () => {
    render(
      <EngagementsTable
        engagements={[
          engagement({
            state: 'active',
            client_organization_id: null,
            client_name: null,
            days_per_week: null,
            waiting: [{ code: 'missing_days_per_week' }, { code: 'missing_client' }],
          }),
        ]}
      />,
    )
    expect(screen.getByText('Mancano i giorni a settimana')).toBeInTheDocument()
    expect(screen.getByText('Manca il cliente')).toBeInTheDocument()
    expect(screen.getByText('Backend developer · Senza cliente')).toBeInTheDocument()
  })

  it('reads «Nascosto» for role, client and fee of a hidden client, and shows no amount', () => {
    render(
      <EngagementsTable
        engagements={[engagement({ redacted: true, role: null, fee: null, client_name: null })]}
      />,
    )
    expect(screen.getAllByText('Nascosto')).toHaveLength(2)
    expect(screen.queryByText(/€/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Backend developer/)).not.toBeInTheDocument()
  })

  it('omits the freelancer’s name on the freelancer’s own tab', () => {
    render(<EngagementsTable engagements={[engagement()]} showFreelancer={false} />)
    expect(screen.queryByText('Giulia Martini')).not.toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Incarico' })).toBeInTheDocument()
    expect(screen.getByText('Backend developer · Alfa Logistica S.r.l.')).toBeInTheDocument()
  })

  it('gives the full role on hover when the cell ellipsises it', () => {
    const role = 'Senior backend developer e referente tecnico per la migrazione dei sistemi'
    render(<EngagementsTable engagements={[engagement({ role })]} />)
    expect(screen.getByTitle(`${role} · Alfa Logistica S.r.l.`)).toBeInTheDocument()
  })

  it('opens the engagement’s own page from the row, and has no selection', async () => {
    render(<EngagementsTable engagements={[engagement()]} />)
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Giulia Martini/ })).toHaveAttribute('href', '/app/engagements/e1')
    await userEvent.click(screen.getByText('dal 02/11/2026, senza data di fine'))
    expect(navigate).toHaveBeenCalledWith({
      to: '/app/engagements/$engagementId',
      params: { engagementId: 'e1' },
    })
  })
})

describe('EngagementsTable on a phone', () => {
  beforeEach(() => viewport(390))

  it('draws one column of cards with the name and pill, «ruolo · cliente» and what it waits for', () => {
    render(<EngagementsTable engagements={[engagement()]} />)
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    const card = within(screen.getByRole('list', { name: 'Incarichi' })).getByRole('link')
    expect(card).toHaveAttribute('href', '/app/engagements/e1')
    expect(within(card).getByText('Giulia Martini')).toBeInTheDocument()
    expect(within(card).getByText('Da inviare')).toBeInTheDocument()
    expect(within(card).getByText('Backend developer · Alfa Logistica S.r.l.')).toBeInTheDocument()
    expect(within(card).getByText('Lettera da scrivere')).toBeInTheDocument()
  })

  it('hides the amounts of a hidden client on a card too', () => {
    render(<EngagementsTable engagements={[engagement({ redacted: true, role: null, fee: null })]} />)
    expect(screen.queryByText(/€/)).not.toBeInTheDocument()
    expect(screen.getAllByText('Nascosto').length).toBeGreaterThan(0)
  })
})
