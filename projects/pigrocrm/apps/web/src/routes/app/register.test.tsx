/**
 * The signup wizard (spec 2026-09-12 §6.4), driven the way a person drives it: the
 * email, then the name. The API client is stubbed at `api.GET`/`api.POST`, the same
 * seam every other route test in this tree uses, and the router is faked because the
 * page only ever calls `navigate`.
 */
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const navigate = vi.fn()
vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useNavigate: () => navigate,
    createFileRoute: () => (options: unknown) => options,
  }
})

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return {
    ...actual,
    api: { GET: vi.fn(), POST: vi.fn() },
  }
})

import { api } from '@/lib/api'
import { writeRegisterHandoffEmail } from '@/lib/registerHandoff'
import { SignupPage } from './register'

const GET = api.GET as unknown as ReturnType<typeof vi.fn>
const POST = api.POST as unknown as ReturnType<typeof vi.fn>

const NOBODY = { membro: false, nome: null, cognome: null, spazi: 0 }
const MEMBER = { membro: true, nome: 'Ada', cognome: 'Lovelace', spazi: 0 }
const OWNER = { membro: true, nome: 'Ada', cognome: 'Lovelace', spazi: 1 }
const OTHER = { membro: true, nome: 'Zoe', cognome: 'Nkosi', spazi: 1 }

/** `POST` answers by path: the member question, the link, the signup. */
function answers(by: Record<string, unknown>) {
  POST.mockImplementation(((path: string) =>
    Promise.resolve({ data: by[path], response: { status: 200 } })) as never)
}

async function throughStepOne(user: ReturnType<typeof userEvent.setup>, email = 'ada@studio.it') {
  await user.type(screen.getByLabelText('Con quale email ti conosciamo?'), email)
  await user.click(screen.getByRole('button', { name: 'Avanti' }))
}

beforeEach(() => {
  GET.mockReset()
  POST.mockReset()
  navigate.mockReset()
  sessionStorage.clear()
  GET.mockResolvedValue({ data: { slug: 'ada-lovelace', disponibile: true } })
})

describe('the signup wizard', () => {
  it('asks the email first and greets a member with the name already written', async () => {
    answers({ '/api/tenants/member': MEMBER })
    const user = userEvent.setup()
    render(<SignupPage />)
    expect(screen.getByText(/1 di 2/)).toBeInTheDocument()
    await throughStepOne(user)
    expect(POST).toHaveBeenCalledWith('/api/tenants/member', { body: { email: 'ada@studio.it' } })
    expect(await screen.findByText(/Sei dei nostri: ciao Ada/)).toBeInTheDocument()
    expect(screen.getByLabelText('Come si chiama il tuo spazio?')).toHaveValue('Ada Lovelace')
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(/\/ada-lovelace è libero/),
    )
    expect(GET).toHaveBeenCalledWith('/api/tenants/{slug}/disponibile', {
      params: { path: { slug: 'ada-lovelace' } },
    })
    expect(screen.queryByLabelText('Password')).toBeNull()
  })

  it('lets somebody who is not a member in anyway, with the name to type', async () => {
    answers({ '/api/tenants/member': NOBODY })
    const user = userEvent.setup()
    render(<SignupPage />)
    await throughStepOne(user, 'bob@studio.it')
    expect(await screen.findByText(/2 di 2/)).toBeInTheDocument()
    expect(screen.getByLabelText('Come si chiama il tuo spazio?')).toHaveValue('')
    expect(screen.getByText(/Non sei ancora nella community/)).toBeInTheDocument()
  })

  it('offers the link by mail to an address that already owns a space, and a way to make another', async () => {
    answers({ '/api/tenants/member': OWNER, '/api/auth/link': { ok: true } })
    const user = userEvent.setup()
    render(<SignupPage />)
    await throughStepOne(user)
    expect(await screen.findByText('Hai già uno spazio')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Mandami il link per entrare' }))
    await waitFor(() =>
      expect(POST).toHaveBeenCalledWith('/api/auth/link', { body: { email: 'ada@studio.it' } }),
    )
    expect(await screen.findByText(/Controlla la posta/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Vuoi crearne un altro?' }))
    expect(screen.getByLabelText('Come si chiama il tuo spazio?')).toBeInTheDocument()
  })

  it('opens the address only on «cambia» and refuses a reserved one before asking the server', async () => {
    answers({ '/api/tenants/member': NOBODY })
    const user = userEvent.setup()
    render(<SignupPage />)
    await throughStepOne(user, 'bob@studio.it')
    await user.type(await screen.findByLabelText('Come si chiama il tuo spazio?'), 'Studio Bob')
    expect(screen.queryByLabelText('Indirizzo dello spazio')).toBeNull()
    await user.click(screen.getByRole('button', { name: 'cambia' }))
    const slug = screen.getByLabelText('Indirizzo dello spazio')
    expect(slug).toHaveValue('studio-bob')
    GET.mockClear()
    await user.clear(slug)
    await user.type(slug, 'app')
    expect(screen.getByRole('status')).toHaveTextContent(/riservato/)
    expect(slug).toHaveAttribute('aria-invalid', 'true')
    expect(GET).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Crea lo spazio' })).toBeDisabled()
  })

  it('creates the space without a password and lands inside it', async () => {
    answers({ '/api/tenants/member': MEMBER, '/api/tenants/': { slug: 'ada-lovelace' } })
    const go = vi.fn()
    const user = userEvent.setup()
    render(<SignupPage go={go} />)
    await throughStepOne(user)
    await screen.findByLabelText('Come si chiama il tuo spazio?')
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Crea lo spazio' })).toBeEnabled(),
    )
    await user.click(screen.getByRole('button', { name: 'Crea lo spazio' }))
    await waitFor(() =>
      expect(POST).toHaveBeenCalledWith('/api/tenants/', {
        body: { slug: 'ada-lovelace', nome: 'Ada Lovelace', email: 'ada@studio.it', membro: true },
      }),
    )
    await waitFor(() => expect(go).toHaveBeenCalledWith('/ada-lovelace/app/'))
  })

  it('shows the problem the API returned and stays on the name', async () => {
    POST.mockImplementation(((path: string) =>
      Promise.resolve(
        path === '/api/tenants/'
          ? {
              error: { type: 'x', title: 'Conflitto', status: 409, detail: 'questo nome è già in uso', code: 'conflict' },
              response: { status: 409 },
            }
          : { data: NOBODY, response: { status: 200 } },
      )) as never)
    const user = userEvent.setup()
    render(<SignupPage />)
    await throughStepOne(user, 'bob@studio.it')
    await user.type(await screen.findByLabelText('Come si chiama il tuo spazio?'), 'Ada Lovelace')
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Crea lo spazio' })).toBeEnabled(),
    )
    await user.click(screen.getByRole('button', { name: 'Crea lo spazio' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/già in uso/))
    expect(screen.getByLabelText('Come si chiama il tuo spazio?')).toBeInTheDocument()
  })

  it('proposes the name to an owner who wants a second space, and lets everyone change the email', async () => {
    answers({ '/api/tenants/member': OWNER })
    const user = userEvent.setup()
    render(<SignupPage />)
    await throughStepOne(user)
    await screen.findByText('Hai già uno spazio')
    await user.click(screen.getByRole('button', { name: 'Vuoi crearne un altro?' }))
    expect(screen.getByLabelText('Come si chiama il tuo spazio?')).toHaveValue('Ada Lovelace')
    // «Indietro» goes to the email, never back to the owner card, and forgets the name.
    await user.click(screen.getByRole('button', { name: 'Indietro' }))
    expect(screen.getByLabelText('Con quale email ti conosciamo?')).toHaveValue('ada@studio.it')
    expect(screen.queryByText('Hai già uno spazio')).toBeNull()
    await throughStepOne(user)
    await user.click(await screen.findByRole('button', { name: 'Cambia email' }))
    expect(screen.getByLabelText('Con quale email ti conosciamo?')).toBeInTheDocument()
  })

  it('trims the email before asking about it', async () => {
    answers({ '/api/tenants/member': NOBODY })
    const user = userEvent.setup()
    render(<SignupPage />)
    await throughStepOne(user, '  bob@studio.it ')
    expect(POST).toHaveBeenCalledWith('/api/tenants/member', { body: { email: 'bob@studio.it' } })
  })

  it('shows the throttle message instead of leaving the availability check stuck (REB-228)', async () => {
    answers({ '/api/tenants/member': NOBODY })
    GET.mockResolvedValueOnce({
      error: { detail: 'Troppe richieste da qui. Riprova tra un minuto.' },
      response: { status: 429 },
    })
    const user = userEvent.setup()
    render(<SignupPage />)
    await throughStepOne(user, 'bob@studio.it')
    await user.type(await screen.findByLabelText('Come si chiama il tuo spazio?'), 'Ada Lovelace')
    expect(await screen.findByRole('alert')).toHaveTextContent(/Troppe richieste/)
    // Honestly disabled -- no fresh "free" answer yet -- but never stuck forever: the
    // "checking" state was dropped back to idle, so a further edit asks again on its
    // own, later budget rather than the wizard being dead-ended by one throttled probe.
    expect(screen.getByRole('button', { name: 'Crea lo spazio' })).toBeDisabled()
    await user.type(screen.getByLabelText('Come si chiama il tuo spazio?'), '2')
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Crea lo spazio' })).toBeEnabled(),
    )
  })

  it('lets the person submit anyway when the availability probe throws (REB-236)', async () => {
    answers({ '/api/tenants/member': NOBODY })
    GET.mockRejectedValue(new Error('network down'))
    const user = userEvent.setup()
    render(<SignupPage />)
    await throughStepOne(user, 'bob@studio.it')
    await user.type(await screen.findByLabelText('Come si chiama il tuo spazio?'), 'Ada Lovelace')
    // The generic Italian fallback, never the "still checking" ellipsis that used to
    // hang forever, alongside the address it is about, and the button is not held
    // hostage by an answer that never came: POST /api/tenants/ validates the slug
    // again on the server.
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(
        /\/ada-lovelace: Si è verificato un errore imprevisto/,
      ),
    )
    expect(screen.getByRole('button', { name: 'Crea lo spazio' })).toBeEnabled()
  })

  it('lets the person submit anyway when the availability probe answers a server error (REB-236)', async () => {
    answers({ '/api/tenants/member': NOBODY })
    GET.mockResolvedValue({
      error: { detail: 'Il servizio non risponde, riprova.' },
      response: { status: 503 },
    })
    const user = userEvent.setup()
    render(<SignupPage />)
    await throughStepOne(user, 'bob@studio.it')
    await user.type(await screen.findByLabelText('Come si chiama il tuo spazio?'), 'Ada Lovelace')
    // The API's own sentence, not a generic one, alongside the address, and the
    // field is not marked invalid: a failed probe is not proof the slug itself is a
    // problem.
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(
        /\/ada-lovelace: Il servizio non risponde/,
      ),
    )
    expect(screen.getByRole('button', { name: 'Crea lo spazio' })).toBeEnabled()
  })

  it('ignores an older probe answer that resolves after a newer one already landed (REB-265)', async () => {
    answers({ '/api/tenants/member': NOBODY })
    let resolveAlpha: (value: unknown) => void = () => {}
    let resolveBeta: (value: unknown) => void = () => {}
    GET.mockImplementationOnce(() => new Promise((resolve) => (resolveAlpha = resolve)))
    GET.mockImplementationOnce(() => new Promise((resolve) => (resolveBeta = resolve)))
    const user = userEvent.setup()
    render(<SignupPage />)
    await throughStepOne(user, 'bob@studio.it')
    await user.type(await screen.findByLabelText('Come si chiama il tuo spazio?'), 'Alpha')
    await user.click(screen.getByRole('button', { name: 'cambia' }))
    const slug = screen.getByLabelText('Indirizzo dello spazio')
    await waitFor(() =>
      expect(GET).toHaveBeenCalledWith('/api/tenants/{slug}/disponibile', {
        params: { path: { slug: 'alpha' } },
      }),
    )
    await user.clear(slug)
    await user.type(slug, 'beta')
    await waitFor(() =>
      expect(GET).toHaveBeenCalledWith('/api/tenants/{slug}/disponibile', {
        params: { path: { slug: 'beta' } },
      }),
    )
    // The newer probe (beta) answers first.
    resolveBeta({ data: { slug: 'beta', disponibile: true }, response: { status: 200 } })
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/\/beta è libero/))
    // The older probe (alpha) answers late, for a slug that is no longer current: it
    // must not overwrite beta's already-landed answer with its own.
    await act(async () => {
      resolveAlpha({
        data: { slug: 'alpha', disponibile: false, motivo: 'riservato' },
        response: { status: 200 },
      })
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(screen.getByRole('status')).toHaveTextContent(/\/beta è libero/)
  })
})

describe('the fast path (REB-488)', () => {
  it('lands on the name step directly, with no email or owner screen, even for an address that already owns a space', async () => {
    writeRegisterHandoffEmail('ada@studio.it')
    answers({ '/api/tenants/member': OWNER })
    render(<SignupPage />)
    expect(screen.getByLabelText('Come si chiama il tuo spazio?')).toBeInTheDocument()
    expect(screen.queryByLabelText('Con quale email ti conosciamo?')).toBeNull()
    expect(screen.queryByText('Hai già uno spazio')).toBeNull()
    expect(await screen.findByText(/Sei dei nostri: ciao Ada/)).toBeInTheDocument()
    expect(screen.getByLabelText('Come si chiama il tuo spazio?')).toHaveValue('Ada Lovelace')
  })

  it('clears the handoff from sessionStorage once read, so a reload of this page never re-arms it', () => {
    writeRegisterHandoffEmail('ada@studio.it')
    answers({ '/api/tenants/member': NOBODY })
    render(<SignupPage />)
    expect(sessionStorage.getItem('pigrocrm:register-handoff-email')).toBeNull()
  })

  it('ignores a handoff written more than 30s ago, the same shared-tab-reuse case Greptile raised', async () => {
    sessionStorage.setItem(
      'pigrocrm:register-handoff-email',
      JSON.stringify({ email: 'ada@studio.it', issuedAt: Date.now() - 60_000 }),
    )
    render(<SignupPage />)
    expect(await screen.findByLabelText('Con quale email ti conosciamo?')).toBeInTheDocument()
    expect(screen.queryByLabelText('Come si chiama il tuo spazio?')).toBeNull()
  })

  it('titles the card for an additional space, not the first one', async () => {
    writeRegisterHandoffEmail('ada@studio.it')
    answers({ '/api/tenants/member': NOBODY })
    render(<SignupPage />)
    expect(await screen.findByText('Crea un nuovo spazio')).toBeInTheDocument()
  })

  it('creates the space with the email the caller already knew, and the membro flag the background lookup answered', async () => {
    writeRegisterHandoffEmail('ada@studio.it')
    answers({ '/api/tenants/member': OWNER, '/api/tenants/': { slug: 'ada-lovelace' } })
    const go = vi.fn()
    render(<SignupPage go={go} />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Crea lo spazio' })).toBeEnabled(),
    )
    await userEvent.click(screen.getByRole('button', { name: 'Crea lo spazio' }))
    await waitFor(() =>
      expect(POST).toHaveBeenCalledWith('/api/tenants/', {
        body: { slug: 'ada-lovelace', nome: 'Ada Lovelace', email: 'ada@studio.it', membro: true },
      }),
    )
    await waitFor(() => expect(go).toHaveBeenCalledWith('/ada-lovelace/app/'))
  })

  it('never overwrites a name the person already started typing while the background lookup is still in flight', async () => {
    writeRegisterHandoffEmail('ada@studio.it')
    let resolveMember: (value: { data: unknown; response: { status: number } }) => void = () => {}
    POST.mockImplementation(((path: string) =>
      path === '/api/tenants/member'
        ? new Promise((resolve) => (resolveMember = resolve))
        : Promise.resolve({ data: {}, response: { status: 200 } })) as never)
    const user = userEvent.setup()
    render(<SignupPage />)
    await user.type(screen.getByLabelText('Come si chiama il tuo spazio?'), 'Studio Bob')
    resolveMember({ data: OWNER, response: { status: 200 } })
    expect(await screen.findByText(/Sei dei nostri/)).toBeInTheDocument()
    expect(screen.getByLabelText('Come si chiama il tuo spazio?')).toHaveValue('Studio Bob')
  })

  it('drops a fast-path answer that lands after the person went back to a different email', async () => {
    writeRegisterHandoffEmail('ada@studio.it')
    let resolveOriginal: (value: { data: unknown; response: { status: number } }) => void = () => {}
    let calls = 0
    POST.mockImplementation(((path: string) => {
      if (path === '/api/tenants/member') {
        calls += 1
        return calls === 1
          ? new Promise((resolve) => (resolveOriginal = resolve))
          : Promise.resolve({ data: MEMBER, response: { status: 200 } })
      }
      return Promise.resolve({ data: {}, response: { status: 200 } })
    }) as never)
    const user = userEvent.setup()
    render(<SignupPage />)
    await user.click(screen.getByRole('button', { name: 'Indietro' }))
    await throughStepOne(user, 'bob@studio.it')
    expect(await screen.findByText(/Sei dei nostri: ciao Ada/)).toBeInTheDocument()
    // The abandoned fast-path lookup (still tied to ada@studio.it) answers late, for
    // an email nobody is looking at anymore -- it must not land over bob's answer.
    resolveOriginal({ data: OTHER, response: { status: 200 } })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(screen.getByText(/Sei dei nostri: ciao Ada/)).toBeInTheDocument()
    expect(screen.queryByText(/ciao Zoe/)).toBeNull()
  })

  it('keeps the copy in step with the form after backing out of the fast path', async () => {
    writeRegisterHandoffEmail('ada@studio.it')
    answers({ '/api/tenants/member': NOBODY })
    const user = userEvent.setup()
    render(<SignupPage />)
    expect(screen.getByText('Crea un nuovo spazio')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Indietro' }))
    expect(screen.getByText('Crea il tuo spazio')).toBeInTheDocument()
    expect(screen.getByText(/1 di 2/)).toBeInTheDocument()
    expect(screen.getByLabelText('Con quale email ti conosciamo?')).toBeInTheDocument()
  })

  it('keeps the create button disabled until the background member lookup settles, even once the slug is free', async () => {
    writeRegisterHandoffEmail('ada@studio.it')
    let resolveMember: (value: { data: unknown; response: { status: number } }) => void = () => {}
    POST.mockImplementation(((path: string) =>
      path === '/api/tenants/member'
        ? new Promise((resolve) => (resolveMember = resolve))
        : Promise.resolve({ data: {}, response: { status: 200 } })) as never)
    const user = userEvent.setup()
    render(<SignupPage />)
    await user.type(screen.getByLabelText('Come si chiama il tuo spazio?'), 'Studio Bob')
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/è libero/))
    expect(screen.getByRole('button', { name: 'Crea lo spazio' })).toBeDisabled()
    resolveMember({ data: MEMBER, response: { status: 200 } })
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Crea lo spazio' })).toBeEnabled(),
    )
  })
})
