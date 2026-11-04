import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemberContacts } from './Contacts'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const LOGIN = { id: null, kind: 'email', value: 'mario@rossi.it', label: 'work', note: null, is_primary: true, position: 0 }
const PHONE = {
  id: 'p1',
  kind: 'phone',
  value: '+39 345 1234567',
  label: 'work',
  note: null,
  is_primary: true,
  position: 1,
}

function profile(points: unknown[]) {
  return {
    id: 'u1',
    first_name: 'Mario',
    last_name: 'Rossi',
    email: 'mario@rossi.it',
    linkedin_url: null,
    phone: '+39 345 1234567',
    contact_points: points,
    role: 'member',
    created_at: '2026-09-10T10:00:00Z',
    updated_at: '2026-09-10T10:00:00Z',
    has_card: false,
    has_company: true,
    complete: false,
    requests: [],
    links: [],
    talent_cloud: false,
  }
}

/** `/me` answers the profile; the PUT is answered by `onPut`, which sees the body sent. */
function mount(onPut: (body: unknown) => Response) {
  const calls: { method: string; body: unknown }[] = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const path = String(input)
    const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined
    calls.push({ method: init?.method ?? 'GET', body })
    if (path === '/api/hub/me/contact-points') return onPut(body)
    return answer(200, profile([LOGIN, PHONE]))
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemberContacts />
    </QueryClientProvider>,
  )
  return calls
}

afterEach(() => vi.restoreAllMocks())

describe('«I tuoi recapiti» in the member area (REB-944)', () => {
  it('lists the sign-in address and the points the person has, with their labels', async () => {
    mount(() => answer(200, profile([LOGIN, PHONE])))
    expect(await screen.findByRole('link', { name: 'mario@rossi.it' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '+39 345 1234567' })).toBeInTheDocument()
    expect(screen.getByText(/\(lavoro\)/)).toBeInTheDocument()
  })

  it('saves the whole list with a second address, and shows what comes back', async () => {
    const extra = {
      id: 'p2',
      kind: 'email',
      value: 'mario.casa@gmail.com',
      label: 'personal',
      note: 'solo per le fatture',
      is_primary: false,
      position: 2,
    }
    const calls = mount(() => answer(200, profile([LOGIN, PHONE, extra])))
    await userEvent.click(await screen.findByRole('button', { name: 'Modifica recapiti' }))
    await userEvent.click(screen.getByRole('button', { name: 'Aggiungi recapito' }))
    await userEvent.click(screen.getByRole('combobox', { name: 'Tipo del recapito 2' }))
    await userEvent.click(screen.getByRole('option', { name: 'Email' }))
    await userEvent.type(screen.getByRole('textbox', { name: 'Valore del recapito 2' }), 'mario.casa@gmail.com')
    await userEvent.click(screen.getByRole('combobox', { name: 'Etichetta del recapito 2' }))
    await userEvent.click(screen.getByRole('option', { name: 'personale' }))
    await userEvent.type(screen.getByRole('textbox', { name: 'Nota del recapito 2' }), 'solo per le fatture')
    await userEvent.click(screen.getByRole('button', { name: 'Salva recapiti' }))

    expect(calls.find((call) => call.method === 'PUT')?.body).toEqual({
      contact_points: [
        { kind: 'phone', value: '+39 345 1234567', label: 'work', note: null, is_primary: true },
        {
          kind: 'email',
          value: 'mario.casa@gmail.com',
          label: 'personal',
          note: 'solo per le fatture',
          is_primary: false,
        },
      ],
    })
    expect(await screen.findByText(/\(personale: solo per le fatture\)/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Salva recapiti' })).toBeNull()
  })

  it('shows the sentence that names an address somebody else holds, and keeps the editor open', async () => {
    mount(() =>
      answer(422, {
        detail: [
          {
            loc: ['body', 'contact_points'],
            msg: '«ada@studio.it» è già l’indirizzo con cui un’altra persona accede',
          },
        ],
      }),
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Modifica recapiti' }))
    await userEvent.click(screen.getByRole('button', { name: 'Aggiungi recapito' }))
    await userEvent.click(screen.getByRole('combobox', { name: 'Tipo del recapito 2' }))
    await userEvent.click(screen.getByRole('option', { name: 'Email' }))
    await userEvent.type(screen.getByRole('textbox', { name: 'Valore del recapito 2' }), 'ada@studio.it')
    await userEvent.click(screen.getByRole('button', { name: 'Salva recapiti' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('«ada@studio.it» è già l’indirizzo con cui')
    expect(screen.getByRole('button', { name: 'Salva recapiti' })).toBeEnabled()
  })

  it('leaves the list alone on «Annulla»', async () => {
    const calls = mount(() => answer(200, profile([LOGIN, PHONE])))
    await userEvent.click(await screen.findByRole('button', { name: 'Modifica recapiti' }))
    await userEvent.click(screen.getByRole('button', { name: 'Togli il recapito 1' }))
    await userEvent.click(screen.getByRole('button', { name: 'Annulla' }))
    expect(screen.getByRole('link', { name: '+39 345 1234567' })).toBeInTheDocument()
    expect(calls.some((call) => call.method === 'PUT')).toBe(false)
  })
})
