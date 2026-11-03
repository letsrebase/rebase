import { describe, expect, it } from 'vitest'
import type { Campaign, CampaignTemplate } from '@/lib/api'
import {
  EMPTY_FORM,
  LINK_URL_INVALID,
  LINK_URL_MISSING,
  LINK_URL_NOT_HTTPS,
  LINK_URL_TOO_LONG,
  formFromCampaign,
  linkProblem,
  metaOptions,
  punycodeDecode,
  payloadOf,
  withMeta,
  withTemplate,
  type CampaignForm,
} from './form'

const LUMA = 'https://lu.ma/rebase-house'
const STATUS: CampaignForm = { ...EMPTY_FORM, journeyStatus: 'complete', subject: 'o', text: 't', buttonText: 'b' }
const LINK: CampaignForm = { ...STATUS, buttonMeta: 'link', buttonUrl: LUMA, action: 'click' }
const TEMPLATE: CampaignTemplate = {
  journey_status: 'missing_cv',
  label: 'Manca solo il CV',
  subject: 'Manca solo il CV',
  text: 'Ciao {nome}',
  button_text: 'Carica il CV',
  button_meta: 'area',
  action: 'cv',
}

describe('the address «Un link» carries (REB-530)', () => {
  it('asks nothing of any other destination', () => {
    expect(linkProblem({ buttonMeta: 'area', buttonUrl: 'nonsense' })).toBeNull()
  })

  it('refuses what the server refuses, with its own sentences', () => {
    const cases: [string, string | null][] = [
      ['', LINK_URL_MISSING],
      ['   ', LINK_URL_MISSING],
      [`  ${LUMA}  `, null],
      ['https://chat.whatsapp.com/AbC123', null],
      ['http://lu.ma/casa', LINK_URL_NOT_HTTPS],
      ['lu.ma/casa', LINK_URL_NOT_HTTPS],
      ['ftp://lu.ma/casa', LINK_URL_NOT_HTTPS],
      ['https://', LINK_URL_INVALID],
      ['https:lu.ma', LINK_URL_INVALID],
      ['https://lu.ma/una casa', LINK_URL_INVALID],
      ['https://ivan:pw@lu.ma/casa', LINK_URL_INVALID],
      ['https://evil.io\\@lu.ma/', LINK_URL_INVALID],
      ['https://lu.ma/\u202Eesac', LINK_URL_INVALID],
      [`https://lu.ma/${'a'.repeat(490)}`, LINK_URL_TOO_LONG],
      // What `new URL` refuses, or reads differently from the server's `urlsplit`
      // (Greptile P1s on #476): the server refuses the same, with the same sentence.
      ['https://[invalid', LINK_URL_INVALID],
      ['https://lu.ma:99999/rebase-house', LINK_URL_INVALID],
      ['https://lu.ma:abc/', LINK_URL_INVALID],
      ['https:///lu.ma', LINK_URL_INVALID],
      ['https://@lu.ma/', LINK_URL_INVALID],
      ['https://a%2eb.com/', LINK_URL_INVALID],
      ['https://999.1.1.1/', LINK_URL_INVALID],
      ['https://127.1/', LINK_URL_INVALID],
      ['https://xn--a.com/', LINK_URL_INVALID],
      ['https://www.xn--a.com/', LINK_URL_INVALID],
      ['https://xn--mnchen-3ya.de/', null],
      ['https://lu.ma:443/rebase-house', null],
      ['https://[::1]:8443/x', null],
      ['https://1.2.3.4/', null],
      ['https://bücher.example/', null],
    ]
    for (const [buttonUrl, expected] of cases) {
      expect([buttonUrl, linkProblem({ buttonMeta: 'link', buttonUrl })]).toEqual([buttonUrl, expected])
    }
  })
})

describe('an `xn--` label, decoded as the server decodes it (Greptile on #476)', () => {
  it('reads what Python\'s punycode codec reads, and nothing where it raises', () => {
    expect(punycodeDecode('mnchen-3ya')).toBe('münchen')
    expect(punycodeDecode('bcher-kva')).toBe('bücher')
    expect(punycodeDecode('zz-')).toBe('zz')
    expect(punycodeDecode('')).toBe('')
    // `xn--a` decodes, but to U+0080, a control character: no browser opens that host.
    expect(punycodeDecode('a')).toBe('\u0080')
    expect(punycodeDecode('ab-cd9')).toBeNull()
    expect(punycodeDecode('ab!')).toBeNull()
  })
})

describe('«Dove porta» and the action (REB-530)', () => {
  it('measures the click on «Un link», and gives the state its action back after it', () => {
    const linked = withMeta({ ...STATUS, action: 'signed_in' }, 'link', 'signed_in')
    expect([linked.buttonMeta, linked.action]).toEqual(['link', 'click'])
    const back = withMeta(linked, 'wizard', 'card_complete')
    expect([back.buttonMeta, back.action]).toEqual(['wizard', 'card_complete'])
  })

  it('brings a filtered list back to «È entrato» after the link, and leaves its own choice otherwise', () => {
    const filters: CampaignForm = { ...STATUS, source: 'filters', action: 'cv' }
    expect(withMeta(filters, 'wizard', undefined).action).toBe('cv')
    expect(withMeta(withMeta(filters, 'link', undefined), 'area', undefined).action).toBe('signed_in')
  })

  it('keeps a «Riscrivi» on its side of the link, whatever it measures', () => {
    const list: CampaignForm = { ...LINK, source: 'list', segueId: 'c1' }
    expect(metaOptions(list).map(([value]) => value)).toEqual(['link'])
    expect(metaOptions({ ...list, action: 'cv' }).map(([value]) => value)).toEqual(['area', 'wizard', 'request'])
    expect(metaOptions(STATUS).map(([value]) => value)).toEqual(['area', 'wizard', 'request', 'link'])
    expect(withMeta({ ...list, action: 'cv', buttonMeta: 'area' }, 'wizard', 'signed_in').action).toBe('cv')
  })

  it('keeps the click when a state is picked after the admin chose «Un link»', () => {
    expect(withTemplate(LINK, TEMPLATE, 'missing_cv', { mail: true, name: false }).action).toBe('click')
    expect(withTemplate(STATUS, TEMPLATE, 'missing_cv', { mail: true, name: false }).action).toBe('cv')
  })

  it('fills the untouched mail from the state and keeps a destination picked before it', () => {
    const blank: CampaignForm = { ...EMPTY_FORM, buttonMeta: 'link', buttonUrl: LUMA, action: 'click' }
    const filled = withTemplate(blank, TEMPLATE, 'missing_cv', { mail: false, name: false, meta: true })
    expect(filled).toMatchObject({
      subject: TEMPLATE.subject,
      text: TEMPLATE.text,
      buttonText: TEMPLATE.button_text,
      buttonMeta: 'link',
      buttonUrl: LUMA,
      action: 'click',
    })
    const wizard = withTemplate({ ...EMPTY_FORM, buttonMeta: 'wizard' }, TEMPLATE, 'missing_cv', { mail: false, name: false, meta: true })
    expect([wizard.buttonMeta, wizard.action, wizard.subject]).toEqual(['wizard', 'cv', TEMPLATE.subject])
    expect(withTemplate(EMPTY_FORM, TEMPLATE, 'missing_cv', { mail: false, name: false }).buttonMeta).toBe('area')
  })
})

describe('what the page saves (REB-530)', () => {
  it('sends the trimmed address with «Un link» and no address key with any other', () => {
    expect(payloadOf({ ...LINK, buttonUrl: ` ${LUMA} ` })).toMatchObject({ button_meta: 'link', button_url: LUMA, action: 'click' })
    const hub = payloadOf({ ...LINK, buttonMeta: 'area', action: 'signed_in' })
    expect(JSON.stringify(hub)).not.toContain('button_url')
    const list = payloadOf({ ...LINK, source: 'list', segueId: 'c1' })
    expect(list).toEqual({ name: 'Nuova campagna', subject: 'o', text: 't', button_text: 'b', button_meta: 'link', button_url: LUMA })
  })

  it('reads a stored address back into the field, and an empty one as nothing typed', () => {
    const stored = { ...({} as Campaign), source: 'status', button_meta: 'link', button_url: LUMA, action: 'click' } as Campaign
    expect(formFromCampaign(stored).buttonUrl).toBe(LUMA)
    expect(formFromCampaign({ ...stored, button_meta: 'area', button_url: null }).buttonUrl).toBe('')
  })
})
