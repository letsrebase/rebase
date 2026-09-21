import { describe, expect, it } from 'vitest'
import { safeAppRedirect, slugProblem, slugify, tenantPrefixFrom } from './tenant'

describe('the space prefix', () => {
  it('is read from /<slug>/app/... and nothing else', () => {
    expect(tenantPrefixFrom('/studio/app/customers/abc')).toBe('/studio')
    expect(tenantPrefixFrom('/studio/app')).toBe('/studio')
    expect(tenantPrefixFrom('/app/customers')).toBe('')
    expect(tenantPrefixFrom('/')).toBe('')
    expect(tenantPrefixFrom('/studio/clienti')).toBe('')
  })

  it('never mistakes a reserved word or a malformed segment for a space', () => {
    expect(tenantPrefixFrom('/app/app/login')).toBe('')
    expect(tenantPrefixFrom('/Studio/app/login')).toBe('')
    expect(tenantPrefixFrom('/-x-/app/login')).toBe('')
    expect(tenantPrefixFrom('/mcp/app/login')).toBe('')
  })
})

describe('slugify', () => {
  it('matches the server rule', () => {
    expect(slugify('Studio Rossi')).toBe('studio-rossi')
    expect(slugify('  Caffè & Co.  ')).toBe('caffe-co')
    expect(slugify('ÀÉÎÕÜ')).toBe('aeiou')
    expect(slugify('x'.repeat(40))).toBe('x'.repeat(32))
  })
})

describe('slugProblem', () => {
  it('accepts a well-formed name', () => {
    expect(slugProblem('studio-rossi')).toBeNull()
  })

  it('refuses short, malformed and reserved names with a reason', () => {
    expect(slugProblem('ab')).toMatch(/almeno 3/)
    expect(slugProblem('Studio')).toMatch(/minuscole/)
    expect(slugProblem('app')).toMatch(/riservato/)
    expect(slugProblem('mcp')).toMatch(/riservato/)
  })
})

describe('safeAppRedirect', () => {
  it('accepts a deep link under /app, search and all', () => {
    expect(safeAppRedirect('/app/invoices/42')).toBe('/app/invoices/42')
    expect(safeAppRedirect('/app/customers?search=rossi')).toBe('/app/customers?search=rossi')
  })

  it('refuses anything not resolving under /app, undefined, a scheme or a protocol-relative address', () => {
    expect(safeAppRedirect(undefined)).toBeUndefined()
    expect(safeAppRedirect('/clienti')).toBeUndefined()
    expect(safeAppRedirect('https://evil.example/steal')).toBeUndefined()
    expect(safeAppRedirect('//evil.example/app/steal')).toBeUndefined()
  })

  it('refuses a `..` segment walking back out of /app, plain or percent-encoded', () => {
    expect(safeAppRedirect('/app/../evil')).toBeUndefined()
    expect(safeAppRedirect('/app/%2e%2e/evil')).toBeUndefined()
  })

  it('refuses the public routes in every spelling, since the guard never records them', () => {
    expect(safeAppRedirect('/app/login')).toBeUndefined()
    expect(safeAppRedirect('/app/login/')).toBeUndefined()
    expect(safeAppRedirect('/app/LOGIN')).toBeUndefined()
    expect(safeAppRedirect('/app/%6cogin')).toBeUndefined()
    expect(safeAppRedirect('/app/register')).toBeUndefined()
    expect(safeAppRedirect('/app/verify')).toBeUndefined()
  })
})
