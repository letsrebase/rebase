/** «Sedi» of a legal entity (REB-1092): listed, added, edited and removed, the server's 409 said as
 *  it says it, and nothing at all for a legal entity that is not a company. */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { LegalEntitySites } from './LegalEntitySites'
import type { LegalEntityRecord, LegalEntitySite } from './queries'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

const LEGAL_ENTITY = { id: 'le-1', name: 'Studio Rossi' } as LegalEntityRecord

const REGIONS = [
  { key: '03', name: 'Lombardia' },
  { key: '05', name: 'Veneto' },
]

const VENETO: LegalEntitySite = {
  id: 'site-1',
  legal_entity_id: 'le-1',
  region: '05',
  region_name: 'Veneto',
  city: 'Padova',
  address: 'Via Roma 3',
  staff_count: 2,
  created_at: '2026-11-01T09:00:00Z',
  updated_at: '2026-11-01T09:00:00Z',
}

const ok = (data: unknown, status = 200) => ({ data, response: new Response(null, { status }) }) as never
const failed = (error: unknown, status: number) => ({ error, response: new Response(null, { status }) }) as never

function serve(pack: string, sites: LegalEntitySite[]) {
  vi.mocked(api.GET).mockImplementation(((path: string) => {
    if (path === '/api/legal-entities/{legal_entity_id}/fiscal-profile') return Promise.resolve(ok({ pack_id: pack }))
    if (path === '/api/legal-entities/{legal_entity_id}/sites') return Promise.resolve(ok(sites))
    if (path === '/api/legal-entities/regions') return Promise.resolve(ok(REGIONS))
    return Promise.reject(new Error(`unexpected ${path}`))
  }) as never)
}

function draw() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <LegalEntitySites legal_entity={LEGAL_ENTITY} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.GET).mockReset()
  vi.mocked(api.POST).mockReset()
  vi.mocked(api.PATCH).mockReset()
  vi.mocked(api.DELETE).mockReset()
})

describe('LegalEntitySites', () => {
  it('lists each site with its region, place and how many people work there', async () => {
    serve('it-company', [VENETO])
    draw()
    expect(await screen.findByRole('heading', { name: 'Sedi' })).toBeInTheDocument()
    expect(await screen.findByText('Veneto')).toBeInTheDocument()
    expect(screen.getByText('Padova, Via Roma 3 · 2 persone')).toBeInTheDocument()
  })

  it('is absent for an azienda that is not on the company pack, and while the profile loads', async () => {
    serve('it-flat-rate', [VENETO])
    draw()
    await waitFor(() =>
      expect(api.GET).toHaveBeenCalledWith('/api/legal-entities/{legal_entity_id}/fiscal-profile', expect.anything()),
    )
    expect(screen.queryByRole('heading', { name: 'Sedi' })).not.toBeInTheDocument()
    expect(api.GET).not.toHaveBeenCalledWith('/api/legal-entities/{legal_entity_id}/sites', expect.anything())
  })

  it('adds a site with the region chosen and the optional place left empty as null', async () => {
    serve('it-company', [])
    vi.mocked(api.POST).mockResolvedValue(ok(VENETO, 201))
    const user = userEvent.setup()
    draw()
    expect(await screen.findByText('Nessuna sede oltre a quella legale.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Aggiungi una sede' }))
    await user.click(screen.getByLabelText('Regione *'))
    await user.click(await screen.findByRole('option', { name: 'Veneto' }))
    await user.type(screen.getByLabelText('Comune'), 'Padova')
    await user.click(screen.getByRole('button', { name: 'Aggiungi' }))
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith('/api/legal-entities/{legal_entity_id}/sites', {
        params: { path: { legal_entity_id: 'le-1' } },
        body: { region: '05', city: 'Padova', address: null },
      }),
    )
  })

  it('asks for the region before sending anything', async () => {
    serve('it-company', [])
    const user = userEvent.setup()
    draw()
    await user.click(await screen.findByRole('button', { name: 'Aggiungi una sede' }))
    await user.click(screen.getByRole('button', { name: 'Aggiungi' }))
    expect(screen.getByText('Scegli la regione.')).toBeInTheDocument()
    expect(api.POST).not.toHaveBeenCalled()
  })

  it('edits a site and sends the fields as they stand, an emptied one as null', async () => {
    serve('it-company', [VENETO])
    vi.mocked(api.PATCH).mockResolvedValue(ok(VENETO))
    const user = userEvent.setup()
    draw()
    await user.click(await screen.findByRole('button', { name: 'Modifica' }))
    await user.clear(screen.getByLabelText('Indirizzo'))
    await user.click(screen.getByRole('button', { name: 'Salva' }))
    await waitFor(() =>
      expect(api.PATCH).toHaveBeenCalledWith('/api/legal-entities/{legal_entity_id}/sites/{site_id}', {
        params: { path: { legal_entity_id: 'le-1', site_id: 'site-1' } },
        body: { region: '05', city: 'Padova', address: null },
      }),
    )
  })

  it('removes a site', async () => {
    serve('it-company', [{ ...VENETO, staff_count: 0 }])
    vi.mocked(api.DELETE).mockResolvedValue(ok(undefined, 204))
    const user = userEvent.setup()
    draw()
    await user.click(await screen.findByRole('button', { name: 'Rimuovi' }))
    await waitFor(() =>
      expect(api.DELETE).toHaveBeenCalledWith('/api/legal-entities/{legal_entity_id}/sites/{site_id}', {
        params: { path: { legal_entity_id: 'le-1', site_id: 'site-1' } },
      }),
    )
  })

  it('shows the server’s sentence when the site still has people', async () => {
    serve('it-company', [VENETO])
    vi.mocked(api.DELETE).mockResolvedValue(
      failed(
        {
          type: 'about:blank',
          title: 'Conflitto',
          status: 409,
          detail: 'la sede ha ancora del personale assegnato: spostalo prima',
          code: 'conflict',
        },
        409,
      ),
    )
    const user = userEvent.setup()
    draw()
    const row = (await screen.findByText('Veneto')).closest('li') as HTMLElement
    await user.click(within(row).getByRole('button', { name: 'Rimuovi' }))
    expect(await within(row).findByRole('alert')).toHaveTextContent(
      'la sede ha ancora del personale assegnato: spostalo prima',
    )
  })
})
