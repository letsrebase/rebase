import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { COMPANY_A, answer, mount, resetMocks, routeFetch } from '../list-test-kit'

afterEach(resetMocks)

describe('REB-356: the state-change mutations keep the notes thread visible', () => {
  it('keeps the existing notes thread after a state change on the company detail', async () => {
    const withComment = {
      ...COMPANY_A,
      note: [
        { id: 'c2', text: 'In attesa di risposta.', author: 'Ivan', author_id: null, source: 'web', created_at: '2026-09-12T10:00:00Z', edited_at: null, edited_by: null, editable: true },
      ],
    }
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/companies/c1/audit') return answer(200, [])
      if (url === '/api/hub/companies/c1' && init?.method === 'PATCH') {
        const body = JSON.parse(init.body as string)
        expect(body.status).toBe('contacted')
        // The moveCompany response's own `note` is always `[]` (only `get` fills
        // it): the old `setQueryData(['company', id], updated)` replaced the cache
        // outright with that empty array, which is exactly what this guards against.
        return answer(200, { ...withComment, status: 'contacted', note: [] })
      }
      return answer(200, withComment)
    })
    mount('/admin/companies/c1')
    await screen.findByRole('heading', { name: 'Rossi Studio' })
    await screen.findByText('In attesa di risposta.')

    await userEvent.click(screen.getByRole('button', { name: 'Contattato' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    const banner = await screen.findByRole('banner')
    await within(banner).findByText('Contattato')
    expect(screen.getByText('In attesa di risposta.')).toBeInTheDocument()
  })
})

describe('the company detail', () => {
  it('shows the request, its referente, its state and the REB-380 fields', async () => {
    routeFetch({
      'GET /api/hub/companies/c1': COMPANY_A,
      'GET /api/hub/companies/c1/audit': [],
    })
    mount('/admin/companies/c1')
    await screen.findByRole('heading', { name: 'Rossi Studio' })
    expect(screen.getByText(/Mario Rossi/)).toBeInTheDocument()
    expect(screen.getByText('Piattaforma di prenotazione')).toBeInTheDocument()
    expect(screen.getByText('Backend developer')).toBeInTheDocument()
    expect(screen.getByText('Ibrido · 3 giorni a settimana in sede')).toBeInTheDocument()
    expect(screen.getByText(/\+39 345 1234567/)).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
  })

  it('overrides a field through "Modifica richiesta" and shows the new value', async () => {
    // The server keeps what the override wrote: the page reads the request again after it.
    let stored = COMPANY_A
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/companies/c1/audit') return answer(200, [])
      if (url === '/api/hub/companies/c1/override' && init?.method === 'PATCH') {
        const body = JSON.parse(init.body as string)
        expect(body.duration).toBe('6 mesi')
        stored = { ...COMPANY_A, duration: '6 mesi' }
        return answer(200, stored)
      }
      return answer(200, stored)
    })
    mount('/admin/companies/c1')
    await screen.findByRole('heading', { name: 'Rossi Studio' })
    await userEvent.click(screen.getByRole('button', { name: 'Modifica richiesta' }))
    const duration = await screen.findByLabelText('Durata')
    await userEvent.clear(duration)
    await userEvent.type(duration, '6 mesi')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(screen.getByText(/6 mesi/)).toBeInTheDocument())
  })

  it('deletes the request, then restores it', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/companies/c1/audit') return answer(200, [])
      if (url === '/api/hub/companies/c1' && init?.method === 'DELETE') {
        return answer(200, { ...COMPANY_A, deleted_at: '2026-09-23T10:00:00Z' })
      }
      if (url === '/api/hub/companies/c1/restore' && init?.method === 'POST') {
        return answer(200, { ...COMPANY_A, deleted_at: null })
      }
      return answer(200, COMPANY_A)
    })
    mount('/admin/companies/c1')
    await screen.findByRole('heading', { name: 'Rossi Studio' })

    await userEvent.click(screen.getByRole('button', { name: 'Elimina' }))
    await screen.findByText(/Eliminata il/)
    expect(screen.queryByRole('button', { name: 'Modifica richiesta' })).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: 'Ripristina' }))
    await waitFor(() => expect(screen.queryByText(/Eliminata il/)).toBeNull())
    expect(screen.getByRole('button', { name: 'Modifica richiesta' })).toBeInTheDocument()
    expect(spy).toHaveBeenCalledWith('/api/hub/companies/c1', expect.objectContaining({ method: 'DELETE' }))
  })
})

describe('a state change that is refused', () => {
  it('says so under the state editor and keeps the old state on the company detail', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/companies/c1/audit') return answer(200, [])
      if (url === '/api/hub/companies/c1' && init?.method === 'PATCH') {
        return answer(409, { detail: 'Lo stato non può cambiare.' })
      }
      return answer(200, COMPANY_A)
    })
    mount('/admin/companies/c1')
    await screen.findByRole('heading', { name: 'Rossi Studio' })

    await userEvent.click(screen.getByRole('button', { name: 'Contattato' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Lo stato non può cambiare.')
    expect(within(screen.getByRole('banner')).getByText('Nuovo')).toBeInTheDocument()
  })
})
