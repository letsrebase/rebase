import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, admin, applyAsFreelancer, requestPeople } from './api'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => vi.restoreAllMocks())

describe('the api client', () => {
  it('turns a 422 into an ApiError that names the fields', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(422, {
        detail: [
          { loc: ['body', 'tariffa_giornaliera'], msg: 'Serve una cifra.' },
          { loc: ['body', 'cv'], msg: 'Il CV deve essere un PDF.' },
        ],
      }),
    )
    const failure = await requestPeople(
      {
        nome_azienda: 'ACME',
        figura_richiesta: 'Backend developer',
        referente_nome: 'Wile',
        referente_cognome: 'E.',
        email: 'w@acme.it',
        telefono: '+39 345 1234567',
        progetto: 'x',
        periodo_da: '2026-10-01',
        durata: '3 mesi',
        budget_giornaliero: '500',
        remoto: 'remoto',
        giorni_presenza: '',
        numero_risorse: '1',
      },
      {},
    ).catch((error: unknown) => error)
    expect(failure).toBeInstanceOf(ApiError)
    const apiError = failure as ApiError
    expect(apiError.status).toBe(422)
    expect(apiError.fields).toEqual(['tariffa_giornaliera', 'cv'])
    expect(apiError.message).toBe('Serve una cifra. · Il CV deve essere un PDF.')
  })

  it('has a sentence for a 429 and for an unexplained failure', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(new Response('', { status: 429 }))
    await expect(admin.talent()).rejects.toMatchObject({ status: 429, message: /Riprova/ })
    spy.mockResolvedValueOnce(new Response('boom', { status: 500 }))
    await expect(admin.talent()).rejects.toMatchObject({ status: 500, message: /500/ })
  })

  it('sends the freelancer as multipart with the UTM and without empty optionals', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(201, { ok: true }))
    const cv = new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], 'cv.pdf', {
      type: 'application/pdf',
    })
    await applyAsFreelancer(
      {
        nome: 'Ada',
        cognome: 'Lovelace',
        email: 'ada@studio.it',
        linkedin_url: '',
        tariffa_giornaliera: '450',
        posizione: 'Backend developer',
        remoto: 'remoto',
        links: ['https://github.com/ada', '  '],
        cv,
      },
      { utm_source: 'linkedin' },
    )
    const [url, init] = spy.mock.calls[0]!
    expect(url).toBe('/api/hub/freelancers')
    const body = init?.body as FormData
    expect(body.get('nome')).toBe('Ada')
    expect(body.get('utm_source')).toBe('linkedin')
    expect(body.has('linkedin_url')).toBe(false)
    expect(body.getAll('links')).toEqual(['https://github.com/ada'])
    expect((body.get('cv') as File).name).toBe('cv.pdf')
  })

  it('lists the admins and promotes one by email, no password anywhere', async () => {
    // ORB-123, REB-279: the list and the promote/demote pair behind «Amministratori».
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(
      answer(200, {
        items: [{ id: '1', email: 'ivan@rebase.it', nome: 'Ivan', attivo: true, created_at: '2026-09-10T10:00:00Z' }],
        next_cursor: null,
      }),
    )
    const listed = await admin.admins()
    expect(spy.mock.calls[0]![0]).toBe('/api/hub/admins')
    expect(listed.items[0]!.email).toBe('ivan@rebase.it')
    expect(listed.next_cursor).toBeNull()

    spy.mockResolvedValueOnce(answer(200, { items: [], next_cursor: null }))
    await admin.admins({ q: 'ivan', cursor: 'abc', limit: 10 })
    expect(spy.mock.calls[1]![0]).toBe('/api/hub/admins?q=ivan&cursor=abc&limit=10')

    spy.mockResolvedValueOnce(answer(200, { id: '2', email: 'lorenzo@rebase.it', nome: 'Lorenzo', attivo: true, created_at: '2026-09-10T10:01:00Z' }))
    const promoted = await admin.promote({ email: 'lorenzo@rebase.it', nome: 'Lorenzo' })
    const [url, init] = spy.mock.calls[2]!
    expect(url).toBe('/api/hub/admins/promote')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({ email: 'lorenzo@rebase.it', nome: 'Lorenzo' })
    expect(promoted.nome).toBe('Lorenzo')
  })

  it('demotes an admin by id, reversibly', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(answer(200, { id: '2', email: 'lorenzo@rebase.it', nome: 'Lorenzo', attivo: true, created_at: '2026-09-10T10:01:00Z' }))
    const demoted = await admin.demote('2')
    const [url, init] = spy.mock.calls[0]!
    expect(url).toBe('/api/hub/admins/2/demote')
    expect(init?.method).toBe('POST')
    expect(demoted.email).toBe('lorenzo@rebase.it')
  })

  it('reads the spaces of PigroCRM from the hub, never from the CRM directly', async () => {
    // ORB-142: the token stays on the server; the browser only ever talks to the hub.
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(answer(200, { totale: 1, items: [{ slug: 'studio-ada', owner_email: 'ada@studio.it', created_at: '2026-09-10T09:00:00Z', url: 'https://pigro.letsrebase.com/studio-ada/app/', membro: null }] }))
    const spaces = await admin.pigroSpaces()
    expect(spy.mock.calls[0]![0]).toBe('/api/hub/pigro/instances')
    expect(spaces.items[0]!.slug).toBe('studio-ada')
  })

  it('points the admin at the CV route by id', () => {
    expect(admin.cvUrl('abc')).toBe('/api/hub/freelancers/abc/cv')
  })
})
