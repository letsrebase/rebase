import { QueryClient } from '@tanstack/react-query'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { COMPANY_A, COMPANY_TEAM_BUILDER, GRANT, answer, mount, resetMocks, routeFetch } from '../list-test-kit'

afterEach(resetMocks)

describe('«da team builder» and the talent cloud on Aziende (REB-518)', () => {
  it('opens the talent cloud to the referente from the request, then revokes it', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/companies/c1/audit') return answer(200, [])
      if (url === '/api/hub/companies/c1/cloud' && init?.method === 'POST') return answer(201, GRANT)
      if (url === '/api/hub/companies/c1/cloud' && init?.method === 'DELETE') {
        return answer(200, { ...GRANT, revoked_at: '2026-09-26T11:00:00Z', revoked_by: 'a1' })
      }
      return answer(200, COMPANY_TEAM_BUILDER)
    })
    mount('/admin/companies/c1')
    await screen.findByRole('heading', { name: 'Rossi Studio' })
    expect(within(screen.getByRole('banner')).getByText('da team builder')).toBeInTheDocument()
    const section = screen.getByRole('region', { name: 'Talent cloud' })
    expect(within(section).getByText(/Il referente non vede il talent cloud/)).toBeInTheDocument()

    await userEvent.click(within(section).getByRole('button', { name: 'Apri il talent cloud' }))
    await within(section).findByText(/Aperto il 26 set 2026 da Ivan per Mario Rossi/)
    expect(spy).toHaveBeenCalledWith('/api/hub/companies/c1/cloud', expect.objectContaining({ method: 'POST' }))

    await userEvent.click(within(section).getByRole('button', { name: 'Revoca il talent cloud' }))
    await within(section).findByRole('button', { name: 'Apri il talent cloud' })
    expect(spy).toHaveBeenCalledWith('/api/hub/companies/c1/cloud', expect.objectContaining({ method: 'DELETE' }))
  })

  it('reads a grant already live and shows the API\'s sentence when a revoke is refused', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/companies/c1/audit') return answer(200, [])
      if (url === '/api/hub/companies/c1/cloud' && init?.method === 'DELETE') {
        return answer(409, { detail: 'Il talent cloud non è aperto per questa azienda.' })
      }
      return answer(200, { ...COMPANY_TEAM_BUILDER, talent_cloud_grant: GRANT })
    })
    mount('/admin/companies/c1')
    const section = await screen.findByRole('region', { name: 'Talent cloud' })
    await userEvent.click(within(section).getByRole('button', { name: 'Revoca il talent cloud' }))
    expect(await within(section).findByRole('alert')).toHaveTextContent(
      'Il talent cloud non è aperto per questa azienda.',
    )
  })

  it('offers no door on a deleted request', async () => {
    routeFetch({
      'GET /api/hub/companies/c1': { ...COMPANY_A, deleted_at: '2026-09-23T10:00:00Z' },
      'GET /api/hub/companies/c1/audit': [],
    })
    mount('/admin/companies/c1')
    await screen.findByRole('heading', { name: 'Rossi Studio' })
    expect(screen.queryByRole('region', { name: 'Talent cloud' })).toBeNull()
  })

  it('keeps a live grant open after «Salva» and «Modifica richiesta», whose answers carry none', async () => {
    const invalidate = vi.spyOn(QueryClient.prototype, 'invalidateQueries')
    let stored = COMPANY_TEAM_BUILDER
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/companies/c1/audit') return answer(200, [])
      // Both answers come from `_to_read`, which leaves the grant `null`: only `get` reads it.
      if (url === '/api/hub/companies/c1' && init?.method === 'PATCH') {
        stored = { ...COMPANY_TEAM_BUILDER, status: 'contacted' }
        return answer(200, stored)
      }
      if (url === '/api/hub/companies/c1/override' && init?.method === 'PATCH') {
        stored = { ...COMPANY_TEAM_BUILDER, status: 'contacted', duration: '6 mesi' }
        return answer(200, stored)
      }
      return answer(200, { ...stored, talent_cloud_grant: GRANT })
    })
    mount('/admin/companies/c1')
    const section = await screen.findByRole('region', { name: 'Talent cloud' })
    expect(within(section).getByRole('button', { name: 'Revoca il talent cloud' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Contattato' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await within(screen.getByRole('banner')).findByText('Contattato')
    expect(within(section).getByRole('button', { name: 'Revoca il talent cloud' })).toBeInTheDocument()
    expect(within(section).queryByRole('button', { name: 'Apri il talent cloud' })).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: 'Modifica richiesta' }))
    const duration = await screen.findByLabelText('Durata')
    await userEvent.clear(duration)
    await userEvent.type(duration, '6 mesi')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() => expect(screen.getByText(/6 mesi/)).toBeInTheDocument())
    expect(
      within(screen.getByRole('region', { name: 'Talent cloud' })).getByRole('button', {
        name: 'Revoca il talent cloud',
      }),
    ).toBeInTheDocument()
    // The override may have renamed the referente the grant names: the request is read again.
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['company', 'c1'] })
  })

  it('reads the request again after a restore, since the grant comes back to life with it', async () => {
    let reads = 0
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/companies/c1/audit') return answer(200, [])
      if (url === '/api/hub/companies/c1' && init?.method === 'DELETE') {
        return answer(200, { ...COMPANY_TEAM_BUILDER, deleted_at: '2026-09-26T10:00:00Z' })
      }
      if (url === '/api/hub/companies/c1/restore' && init?.method === 'POST') {
        return answer(200, COMPANY_TEAM_BUILDER)
      }
      // The first read has no grant; one opened meanwhile shows only on a fresh read.
      reads += 1
      return answer(200, { ...COMPANY_TEAM_BUILDER, talent_cloud_grant: reads > 1 ? GRANT : null })
    })
    mount('/admin/companies/c1')
    await screen.findByRole('button', { name: 'Apri il talent cloud' })

    await userEvent.click(screen.getByRole('button', { name: 'Elimina' }))
    await screen.findByText(/Eliminata il/)
    expect(screen.queryByRole('region', { name: 'Talent cloud' })).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: 'Ripristina' }))
    await screen.findByRole('button', { name: 'Revoca il talent cloud' })
    expect(reads).toBe(2)
  })

  it('says «Apro…» and «Revoco…» while the answer is on its way', async () => {
    const opening = Promise.withResolvers<Response>()
    const revoking = Promise.withResolvers<Response>()
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/hub/companies/c1/audit') return answer(200, [])
      if (url === '/api/hub/companies/c1/cloud' && init?.method === 'POST') return opening.promise
      if (url === '/api/hub/companies/c1/cloud' && init?.method === 'DELETE') return revoking.promise
      return answer(200, COMPANY_TEAM_BUILDER)
    })
    mount('/admin/companies/c1')
    const section = await screen.findByRole('region', { name: 'Talent cloud' })

    await userEvent.click(within(section).getByRole('button', { name: 'Apri il talent cloud' }))
    expect(await within(section).findByRole('button', { name: 'Apro…' })).toBeDisabled()
    opening.resolve(answer(201, GRANT))
    await userEvent.click(await within(section).findByRole('button', { name: 'Revoca il talent cloud' }))
    expect(await within(section).findByRole('button', { name: 'Revoco…' })).toBeDisabled()
    revoking.resolve(answer(200, { ...GRANT, revoked_at: '2026-09-26T11:00:00Z' }))
    await within(section).findByRole('button', { name: 'Apri il talent cloud' })
  })
})
