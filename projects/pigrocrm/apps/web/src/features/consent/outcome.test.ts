import { describe, expect, it } from 'vitest'
import { consentOutcome, type ConsentProvider } from './outcome'

const PROVIDERS: ConsentProvider[] = ['gmail', 'drive', 'slack']

describe.each(PROVIDERS)('the %s consent outcome', (provider) => {
  it('has a sentence for each of the four codes and none for anything else', () => {
    for (const code of ['collegato', 'negato', 'errore', 'sessione']) {
      expect(consentOutcome(provider, code).messaggio).toEqual(expect.any(String))
    }
    expect(consentOutcome(provider, '<b>ciao</b>').messaggio).toBeNull()
    expect(consentOutcome(provider, undefined).messaggio).toBeNull()
  })

  it('reads inherited property names as no outcome at all', () => {
    for (const crafted of ['__proto__', 'constructor', 'toString', 'hasOwnProperty']) {
      expect(consentOutcome(provider, crafted)).toEqual({ messaggio: null, riprova: null })
    }
  })

  it('offers «Riprova» only after a session that ran out, back to the provider’s own start', () => {
    expect(consentOutcome(provider, 'sessione').riprova).toBe(`/api/${provider}/oauth/start`)
    for (const code of ['collegato', 'negato', 'errore', undefined]) {
      expect(consentOutcome(provider, code).riprova).toBeNull()
    }
  })
})
