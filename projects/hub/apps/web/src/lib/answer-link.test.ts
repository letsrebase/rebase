/** REB-517: the availability mail's token leaves the URL before anything can see it. */
import { afterEach, describe, expect, it } from 'vitest'
import { stripAnswerLink, takeAnswerLink } from './answer-link'

function setLocation(path: string) {
  window.history.replaceState(null, '', path)
}

afterEach(() => {
  setLocation('/hub/')
  takeAnswerLink()
})

describe('stripAnswerLink', () => {
  it('takes the token and the answer out of the URL', () => {
    setLocation('/hub/team/risposta?t=abc_-1&r=no')
    stripAnswerLink()
    expect(window.location.pathname).toBe('/hub/team/risposta')
    expect(window.location.search).toBe('')
    expect(takeAnswerLink()).toEqual({ t: 'abc_-1', r: 'no' })
  })

  it('keeps any other param', () => {
    setLocation('/hub/team/risposta?t=abc&r=si&x=1')
    stripAnswerLink()
    expect(window.location.search).toBe('?x=1')
    expect(takeAnswerLink()).toEqual({ t: 'abc', r: 'si' })
  })

  it('is consumed once: a second read finds nothing', () => {
    setLocation('/hub/team/risposta?t=abc&r=si')
    stripAnswerLink()
    expect(takeAnswerLink()).not.toBeNull()
    expect(takeAnswerLink()).toBeNull()
  })

  it('does nothing off the answer page, or on it with no token', () => {
    setLocation('/hub/team?t=abc&r=si')
    stripAnswerLink()
    expect(window.location.search).toBe('?t=abc&r=si')
    expect(takeAnswerLink()).toBeNull()

    setLocation('/hub/team/risposta?r=si')
    stripAnswerLink()
    expect(window.location.search).toBe('?r=si')
    expect(takeAnswerLink()).toBeNull()
  })
})
