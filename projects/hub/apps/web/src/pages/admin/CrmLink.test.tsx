import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/api'
import { dealIdOf } from '@/lib/crm'
import { CrmLink } from './CrmLink'

const DEAL = '0192e0a0-0000-7000-8000-00000000d3a1'

type State = 'to_link' | 'linked' | 'error' | 'rejected' | null

function mount(crm_status: State, extra: Partial<Parameters<typeof CrmLink>[0]['company']> = {}) {
  const onLink = vi.fn()
  render(
    <CrmLink
      company={{
        crm_status,
        crm_situation: null,
        crm_deal_url: null,
        crm_attempted_at: null,
        ...extra,
      }}
      pending={false}
      onLink={onLink}
      error={null}
    />,
  )
  return onLink
}

describe('CrmLink', () => {
  it('reads the deal id out of an id or an address', () => {
    expect(dealIdOf(DEAL)).toBe(DEAL)
    expect(dealIdOf(`https://pigro.test/rebase/app/deal/${DEAL.toUpperCase()}?x=1`)).toBe(DEAL)
    expect(dealIdOf('nessun deal')).toBeNull()
  })

  it('shows the CRM’s sentence for a request in error and «Riprova» files it', async () => {
    const onLink = mount('error', {
      crm_situation: 'Il CRM non ha risposto: Spazio dei lead non configurato.',
    })
    expect(screen.getByText('Non ancora registrata nel CRM.')).toBeInTheDocument()
    expect(screen.getByText('Il CRM non ha risposto: Spazio dei lead non configurato.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Riprova' }))
    expect(onLink).toHaveBeenCalledWith()
  })

  it('links to the deal once linked and offers no «Riprova»', () => {
    mount('linked', { crm_deal_url: `https://pigro.test/rebase/app/deal/${DEAL}` })
    expect(screen.getByText('Registrata nel CRM.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Apri il deal nel CRM' })).toHaveAttribute(
      'href',
      `https://pigro.test/rebase/app/deal/${DEAL}`,
    )
    expect(screen.queryByRole('button', { name: 'Riprova' })).toBeNull()
  })

  it('a request from before the link is registered by an admin', async () => {
    const onLink = mount(null)
    expect(screen.getByText(/arrivata prima del collegamento/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Registra nel CRM' }))
    expect(onLink).toHaveBeenCalledWith()
  })

  it('«Collega a un altro deal» sends the deal’s id and refuses text with none', async () => {
    const onLink = mount('linked')
    const field = screen.getByLabelText('Collega a un altro deal')
    await userEvent.type(field, 'un deal')
    await userEvent.click(screen.getByRole('button', { name: 'Collega al deal' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Non trovo l’id di un deal')
    expect(onLink).not.toHaveBeenCalled()

    await userEvent.clear(field)
    await userEvent.type(field, `https://pigro.test/x/deal/${DEAL}`)
    await userEvent.click(screen.getByRole('button', { name: 'Collega al deal' }))
    expect(onLink).toHaveBeenCalledWith(DEAL)
  })

  it('says why a refused link failed', () => {
    render(
      <CrmLink
        company={{ crm_status: 'linked', crm_situation: null, crm_deal_url: null, crm_attempted_at: null }}
        pending={false}
        onLink={vi.fn()}
        error={new ApiError(409, 'Il CRM ha rifiutato la richiesta: deal_id: non valido.')}
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('deal_id: non valido')
  })

  it('offers no link to a deal whose address is not a web address', () => {
    mount('linked', { crm_deal_url: 'javascript:alert(1)' })
    expect(screen.queryByRole('link', { name: 'Apri il deal nel CRM' })).toBeNull()
  })
})
