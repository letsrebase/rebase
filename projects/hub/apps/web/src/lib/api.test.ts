import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, admin, applyAsFreelancer, member, requestPeople } from './api'

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
          { loc: ['body', 'daily_rate'], msg: 'Serve una cifra.' },
          { loc: ['body', 'cv'], msg: 'Il CV deve essere un PDF.' },
        ],
      }),
    )
    const failure = await requestPeople(
      {
        company_name: 'ACME',
        requested_role: 'Backend developer',
        contact_first_name: 'Wile',
        contact_last_name: 'E.',
        email: 'w@acme.it',
        phone: '+39 345 1234567',
        project: 'x',
        period_from: '2026-10-01',
        duration: '3 mesi',
        daily_budget: '500',
        remote: 'remote',
        onsite_days: '',
        headcount: '1',
      },
      {},
    ).catch((error: unknown) => error)
    expect(failure).toBeInstanceOf(ApiError)
    const apiError = failure as ApiError
    expect(apiError.status).toBe(422)
    expect(apiError.fields).toEqual(['daily_rate', 'cv'])
    expect(apiError.message).toBe('Serve una cifra. · Il CV deve essere un PDF.')
  })

  it('has a sentence for a 429 and for an unexplained failure', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(new Response('', { status: 429 }))
    await expect(admin.talent()).rejects.toMatchObject({ status: 429, message: /Riprova/ })
    spy.mockResolvedValueOnce(new Response('boom', { status: 500 }))
    await expect(admin.talent()).rejects.toMatchObject({ status: 500, message: /500/ })
  })

  it('shows the server sentence of a 429 as it is', async () => {
    const sentence = 'Oggi abbiamo inviato troppi link di accesso. Riprova domani.'
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: sentence }), {
        status: 429,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    await expect(member.requestLink('ada@studio.it')).rejects.toMatchObject({ status: 429, message: sentence })
  })

  it('sends the freelancer as multipart with the UTM and without empty optionals', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(201, { ok: true }))
    const cv = new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], 'cv.pdf', {
      type: 'application/pdf',
    })
    await applyAsFreelancer(
      {
        first_name: 'Ada',
        last_name: 'Lovelace',
        email: 'ada@studio.it',
        linkedin_url: '',
        daily_rate: '450',
        job_title: 'Backend developer',
        remote: 'remote',
        links: ['https://github.com/ada', '  '],
        cv,
      },
      { utm_source: 'linkedin' },
    )
    const [url, init] = spy.mock.calls[0]!
    expect(url).toBe('/api/hub/freelancers')
    const body = init?.body as FormData
    expect(body.get('first_name')).toBe('Ada')
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
        items: [{ id: '1', email: 'ivan@rebase.it', first_name: 'Ivan', active: true, created_at: '2026-09-10T10:00:00Z' }],
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

    spy.mockResolvedValueOnce(answer(200, { id: '2', email: 'lorenzo@rebase.it', first_name: 'Lorenzo', active: true, created_at: '2026-09-10T10:01:00Z' }))
    const promoted = await admin.promote({ email: 'lorenzo@rebase.it', first_name: 'Lorenzo' })
    const [url, init] = spy.mock.calls[2]!
    expect(url).toBe('/api/hub/admins/promote')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({ email: 'lorenzo@rebase.it', first_name: 'Lorenzo' })
    expect(promoted.first_name).toBe('Lorenzo')
  })

  it('demotes an admin by id, reversibly', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(answer(200, { id: '2', email: 'lorenzo@rebase.it', first_name: 'Lorenzo', active: true, created_at: '2026-09-10T10:01:00Z' }))
    const demoted = await admin.demote('2')
    const [url, init] = spy.mock.calls[0]!
    expect(url).toBe('/api/hub/admins/2/demote')
    expect(init?.method).toBe('POST')
    expect(demoted.email).toBe('lorenzo@rebase.it')
  })

  it('reads the duplicate pairs, and merges one card into another by id (REB-800)', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(answer(200, { mergeable: [], to_review: [], signups: 2 }))
    const list = await admin.duplicates()
    expect(spy.mock.calls[0]![0]).toBe('/api/hub/talent/duplicates')
    expect(list.signups).toBe(2)

    spy.mockResolvedValueOnce(
      answer(200, {
        survivor_freelancer_id: 'f2',
        absorbed_freelancer_id: 'f1',
        action_id: 'a1',
        moved: { logins: 3 },
        left: {},
        cv: 'invariato',
        card_to_regenerate: false,
      }),
    )
    const merged = await admin.mergeFreelancer('f2', 'f1')
    const [url, init] = spy.mock.calls[1]!
    expect(url).toBe('/api/hub/freelancers/f2/merge')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({ da: 'f1' })
    expect(merged.moved).toEqual({ logins: 3 })
  })

  it('carries the sentence of a refused merge, as it is', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(409, { detail: 'Hanno due codici fiscali diversi.' }))
    await expect(admin.mergeFreelancer('f2', 'f1')).rejects.toMatchObject({
      status: 409,
      message: 'Hanno due codici fiscali diversi.',
    })
  })

  it('reads the spaces of PigroCRM from the hub, never from the CRM directly', async () => {
    // ORB-142: the token stays on the server; the browser only ever talks to the hub.
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(answer(200, { total: 1, items: [{ slug: 'studio-ada', owner_email: 'ada@studio.it', created_at: '2026-09-10T09:00:00Z', url: 'https://pigro.letsrebase.com/studio-ada/app/', member: null }] }))
    const spaces = await admin.pigroSpaces()
    expect(spy.mock.calls[0]![0]).toBe('/api/hub/pigro/instances')
    expect(spaces.items[0]!.slug).toBe('studio-ada')
  })

  it('names the anonymous CV after the UTF-8 filename first, and never with a path or a control character', async () => {
    const pdf = (disposition: string) =>
      new Response('%PDF-1.7', {
        status: 200,
        headers: { 'Content-Type': 'application/pdf', 'Content-Disposition': disposition },
      })
    const fetch = vi.spyOn(globalThis, 'fetch')

    fetch.mockResolvedValueOnce(
      pdf(`attachment; filename="rebase-cv-qualita-1a2b3c4d.pdf"; filename*=UTF-8''rebase-cv-qualit%C3%A0-1a2b3c4d.pdf`),
    )
    expect((await admin.downloadAnonymousCv('f1')).filename).toBe('rebase-cv-qualit\u00e0-1a2b3c4d.pdf')

    fetch.mockResolvedValueOnce(pdf(`attachment; filename*=UTF-8''..%2F..%2Fetc%5Crebase%07-cv%0A.pdf`))
    expect((await admin.downloadAnonymousCv('f1')).filename).toBe('....etcrebase-cv.pdf')

    fetch.mockResolvedValueOnce(pdf('attachment; filename="/"'))
    expect((await admin.downloadAnonymousCv('f1')).filename).toBe('cv-anonimo.pdf')
  })

  it('encodes an id that is not a plain slug in the path, and an href too', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { id: 'a/b' }))
    await admin.freelancer('a/b?x')
    expect(spy.mock.calls[0]![0]).toBe('/api/hub/freelancers/a%2Fb%3Fx')
    expect(admin.cvUrl('a/b')).toBe('/api/hub/freelancers/a%2Fb/cv')
  })

  it('sends only the filters that were set, and resolves a 204 with nothing', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
    spy.mockResolvedValueOnce(answer(200, { total: 0, items: [], by_status: {}, next_cursor: null }))
    await admin.talent({ status: 'lead', q: '', job_title: undefined, has_cv: false })
    expect(spy.mock.calls[0]![0]).toBe('/api/hub/talent?status=lead&has_cv=false')

    spy.mockResolvedValueOnce(new Response(null, { status: 204 }))
    await expect(admin.deleteNote('n1')).resolves.toBeUndefined()
    expect(spy.mock.calls[1]![0]).toBe('/api/hub/notes/n1')
    expect(spy.mock.calls[1]![1]).toMatchObject({ method: 'DELETE', credentials: 'same-origin' })
  })

  it('points the admin at the CV route by id', () => {
    expect(admin.cvUrl('abc')).toBe('/api/hub/freelancers/abc/cv')
  })
})
