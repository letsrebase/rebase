import { describe, expect, it } from 'vitest'
import {
  SHELL_CACHE_PREFIX,
  decideFetch,
  shellCacheName,
  staleCacheNames,
  type FetchInput,
} from '../sw/policy'

const ORIGIN = 'https://pigro.example'
const SHELL = new Set(['/app/assets/offline-AAA.js', '/app/assets/tokens-BBB.css'])

function decide(overrides: Partial<FetchInput> & { href?: string }) {
  const { href = `${ORIGIN}/studio/app/customers`, ...rest } = overrides
  return decideFetch({
    method: 'GET',
    mode: 'cors',
    url: new URL(href),
    origin: ORIGIN,
    shellPaths: SHELL,
    ...rest,
  })
}

describe('decideFetch', () => {
  it('sends a page load to the network, and only falls back to the offline page there', () => {
    expect(decide({ mode: 'navigate' })).toBe('navigate')
  })

  it('answers the files the offline page is drawn with from the precache', () => {
    expect(decide({ href: `${ORIGIN}/app/assets/offline-AAA.js` })).toBe('shell')
    expect(decide({ href: `${ORIGIN}/app/assets/tokens-BBB.css?x=1` })).toBe('shell')
  })

  it('never touches an API call, whatever space it is in', () => {
    expect(decide({ href: `${ORIGIN}/studio/api/customers` })).toBe('network')
    expect(decide({ href: `${ORIGIN}/api/customers`, mode: 'same-origin' })).toBe('network')
  })

  it('never touches an asset the application loads for itself', () => {
    expect(decide({ href: `${ORIGIN}/app/assets/index-CCC.js` })).toBe('network')
  })

  it('leaves every write and every other origin alone', () => {
    expect(decide({ method: 'POST', mode: 'navigate' })).toBe('network')
    expect(decide({ method: 'DELETE', href: `${ORIGIN}/app/assets/offline-AAA.js` })).toBe('network')
    expect(decide({ href: 'https://eu.i.posthog.com/e', mode: 'navigate' })).toBe('network')
    expect(decide({ href: `https://cdn.example/app/assets/offline-AAA.js` })).toBe('network')
  })
})

describe('staleCacheNames', () => {
  const scope = 'https://pigro.example/studio/app/'
  const current = shellCacheName(scope, 'new')

  it('deletes the caches of earlier deploys of this space', () => {
    expect(staleCacheNames([shellCacheName(scope, 'old'), current], current, scope)).toEqual([
      shellCacheName(scope, 'old'),
    ])
  })

  it('keeps the caches of every other space: their worker may not have updated yet', () => {
    const other = shellCacheName('https://pigro.example/other/app/', 'old')
    expect(staleCacheNames([other, current], current, scope)).toEqual([])
  })

  it('keeps every cache this worker did not make', () => {
    expect(staleCacheNames(['pigrocrm-push-1', 'workbox-precache', current], current, scope)).toEqual([])
    expect(current.startsWith(SHELL_CACHE_PREFIX)).toBe(true)
  })
})
