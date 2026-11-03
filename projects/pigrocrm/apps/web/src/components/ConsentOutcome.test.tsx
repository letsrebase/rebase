/**
 * The outcome of a consent flow, on whichever page the callback landed, and the
 * «Riprova» a consent that came back with no session gets (REB-446), as the one
 * `consentOutcome` table renders them.
 */
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ConsentOutcome } from './ConsentOutcome'
import { consentOutcome, type ConsentProvider } from '@/features/consent/outcome'

const auth = { canWrite: true }
vi.mock('@/lib/auth', () => ({ useCanWrite: () => auth.canWrite }))

afterEach(() => {
  auth.canWrite = true
})

const SESSIONE_GMAIL = 'La sessione è scaduta mentre eri su Google, quindi la casella non è stata collegata.'
const SESSIONE_DRIVE = 'La sessione è scaduta mentre eri su Google, quindi Drive non è stato collegato.'

function outcome(provider: ConsentProvider, esito: string) {
  return render(<ConsentOutcome {...consentOutcome(provider, esito)} />)
}

describe.each([
  ['gmail', '/api/gmail/oauth/start', SESSIONE_GMAIL],
  ['drive', '/api/drive/oauth/start', SESSIONE_DRIVE],
] as const)('the %s consent outcome', (provider, start, sessione) => {
  it('says the session ran out, in Italian, and offers to start the consent again', () => {
    outcome(provider, 'sessione')
    expect(screen.getByRole('status')).toHaveTextContent(sessione)
    expect(screen.getByRole('link', { name: 'Riprova' })).toHaveAttribute('href', start)
  })

  it('offers «Riprova» under no other outcome', () => {
    for (const esito of ['collegato', 'negato', 'errore']) {
      expect(consentOutcome(provider, esito).riprova).toBeNull()
      const { unmount } = outcome(provider, esito)
      expect(screen.getByRole('status')).toBeInTheDocument()
      expect(screen.queryByRole('link')).toBeNull()
      unmount()
    }
  })

  it('renders nothing for a code the table does not know', () => {
    const { container } = outcome(provider, '<b>scrivi qui</b>')
    expect(container).toBeEmptyDOMElement()
  })
})

describe('«Riprova» for a readonly person', () => {
  it('is left out, since the start it leads to would refuse them', () => {
    auth.canWrite = false
    outcome('gmail', 'sessione')
    expect(screen.getByRole('status')).toHaveTextContent('La sessione è scaduta')
    expect(screen.queryByRole('link', { name: 'Riprova' })).toBeNull()
  })
})
