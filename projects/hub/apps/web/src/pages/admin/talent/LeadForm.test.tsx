import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { INCOMPLETE, LEAD_TALENT, answer, mount, resetMocks } from '../list-test-kit'

afterEach(resetMocks)

describe('a lead offers to draft a card in place (ORB-155, REB-283)', () => {
  it('shows what the sign-up says, drafts a card from the given sources, and opens the new card', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (init?.method === 'POST') {
        expect(url).toBe('/api/hub/signups/s2/card')
        expect(JSON.parse(init.body as string)).toEqual({
          nome: 'Bob',
          last_name: 'Ross',
          linkedin_url: 'https://www.linkedin.com/in/bob',
          links: [],
          sources: ['https://bob.dev'],
        })
        return answer(201, { ...INCOMPLETE, id: 'f9', nome: 'Bob', last_name: 'Ross' })
      }
      if (url === '/api/hub/freelancers/f9') {
        return answer(200, { ...INCOMPLETE, id: 'f9', nome: 'Bob', last_name: 'Ross' })
      }
      if (url === '/api/hub/freelancers/f9/audit') {
        return answer(200, [])
      }
      return answer(200, LEAD_TALENT)
    })
    mount('/admin/talent/s2')

    await screen.findByRole('heading', { name: 'Bob Ross' })
    expect(screen.getByDisplayValue('Bob')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Ross')).toBeInTheDocument()

    await userEvent.type(screen.getByLabelText('Fonti'), 'https://bob.dev')
    await userEvent.click(screen.getByRole('button', { name: 'Crea scheda' }))

    // Landing on the existing freelancer detail (not rewritten here, REB-284's job):
    // its own ownership sentence for a card an admin wrote is proof the redirect worked,
    // and the GET above proves the redirect's $id is the card the POST actually created.
    expect(await screen.findByText('scritta dall’admin, da completare')).toBeInTheDocument()
    expect(spy).toHaveBeenCalled()
  })

  it('drafts a card with a rate typed as «1.500» as 1500 (REB-485)', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (init?.method === 'POST') return answer(201, { ...INCOMPLETE, id: 'f9', nome: 'Bob', last_name: 'Ross' })
      if (url === '/api/hub/freelancers/f9') {
        return answer(200, { ...INCOMPLETE, id: 'f9', nome: 'Bob', last_name: 'Ross' })
      }
      if (url === '/api/hub/freelancers/f9/audit') return answer(200, [])
      return answer(200, LEAD_TALENT)
    })
    mount('/admin/talent/s2')

    await screen.findByRole('heading', { name: 'Bob Ross' })
    await userEvent.type(screen.getByLabelText('Tariffa a giornata'), '1.500')
    await userEvent.type(screen.getByLabelText('Fonti'), 'https://bob.dev')
    await userEvent.click(screen.getByRole('button', { name: 'Crea scheda' }))

    await screen.findByText('scritta dall’admin, da completare')
    const post = spy.mock.calls.find(([, init]) => init?.method === 'POST')!
    expect(post[0]).toBe('/api/hub/signups/s2/card')
    expect(JSON.parse(post[1]!.body as string).daily_rate).toBe('1500.00')
  })

  it.each(['1,000.00', '12,345', '1e3'])(
    'refuses a rate typed as «%s» before any request (REB-485)',
    async (typed) => {
      const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => answer(200, LEAD_TALENT))
      mount('/admin/talent/s2')
      await screen.findByRole('heading', { name: 'Bob Ross' })
      await userEvent.type(screen.getByLabelText('Tariffa a giornata'), typed)
      await userEvent.type(screen.getByLabelText('Fonti'), 'https://bob.dev')
      expect(screen.getByRole('alert')).toHaveTextContent('Serve una cifra, in euro.')
      expect(screen.getByLabelText('Tariffa a giornata')).toHaveAttribute('aria-invalid', 'true')
      await userEvent.click(screen.getByRole('button', { name: 'Crea scheda' }))
      expect(spy.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
    },
  )

  it('refuses without at least one source, since a card written from research needs one', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, LEAD_TALENT))
    mount('/admin/talent/s2')
    await screen.findByRole('heading', { name: 'Bob Ross' })
    expect(screen.getByLabelText('Fonti')).toBeRequired()
  })

  it('reads the one sign-up by its id, never the list of every lead', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => answer(200, LEAD_TALENT))
    mount('/admin/talent/s2')
    await screen.findByRole('heading', { name: 'Bob Ross' })
    const urls = spy.mock.calls.map(([input]) => String(input))
    expect(urls).toContain('/api/hub/signups/s2')
    expect(urls.some((url) => url.startsWith('/api/hub/talent?'))).toBe(false)
  })

  it('says so when the sign-up does not exist', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(404, { detail: 'Non trovato.' }))
    mount('/admin/talent/s9')
    expect(await screen.findByText('Lead non trovato.')).toBeInTheDocument()
  })
})
