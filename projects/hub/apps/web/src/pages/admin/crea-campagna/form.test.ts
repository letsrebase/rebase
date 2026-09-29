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
const STATO: CampaignForm = { ...EMPTY_FORM, statoPercorso: 'completo', oggetto: 'o', testo: 't', bottoneTesto: 'b' }
const LINK: CampaignForm = { ...STATO, bottoneMeta: 'link', bottoneUrl: LUMA, azione: 'clic' }
const TEMPLATE: CampaignTemplate = {
  stato_percorso: 'manca_cv',
  etichetta: 'Manca solo il CV',
  oggetto: 'Manca solo il CV',
  testo: 'Ciao {nome}',
  bottone_testo: 'Carica il CV',
  bottone_meta: 'area',
  azione: 'cv',
}

describe('the address «Un link» carries (REB-530)', () => {
  it('asks nothing of any other destination', () => {
    expect(linkProblem({ bottoneMeta: 'area', bottoneUrl: 'nonsense' })).toBeNull()
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
    for (const [bottoneUrl, expected] of cases) {
      expect([bottoneUrl, linkProblem({ bottoneMeta: 'link', bottoneUrl })]).toEqual([bottoneUrl, expected])
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
    const linked = withMeta({ ...STATO, azione: 'entrato' }, 'link', 'entrato')
    expect([linked.bottoneMeta, linked.azione]).toEqual(['link', 'clic'])
    const back = withMeta(linked, 'wizard', 'scheda_completa')
    expect([back.bottoneMeta, back.azione]).toEqual(['wizard', 'scheda_completa'])
  })

  it('brings a filtered list back to «È entrato» after the link, and leaves its own choice otherwise', () => {
    const filtri: CampaignForm = { ...STATO, fonte: 'filtri', azione: 'cv' }
    expect(withMeta(filtri, 'wizard', undefined).azione).toBe('cv')
    expect(withMeta(withMeta(filtri, 'link', undefined), 'area', undefined).azione).toBe('entrato')
  })

  it('keeps a «Riscrivi» on its side of the link, whatever it measures', () => {
    const lista: CampaignForm = { ...LINK, fonte: 'lista', segueId: 'c1' }
    expect(metaOptions(lista).map(([value]) => value)).toEqual(['link'])
    expect(metaOptions({ ...lista, azione: 'cv' }).map(([value]) => value)).toEqual(['area', 'wizard', 'richiesta'])
    expect(metaOptions(STATO).map(([value]) => value)).toEqual(['area', 'wizard', 'richiesta', 'link'])
    expect(withMeta({ ...lista, azione: 'cv', bottoneMeta: 'area' }, 'wizard', 'entrato').azione).toBe('cv')
  })

  it('keeps the click when a state is picked after the admin chose «Un link»', () => {
    expect(withTemplate(LINK, TEMPLATE, 'manca_cv', { mail: true, nome: false }).azione).toBe('clic')
    expect(withTemplate(STATO, TEMPLATE, 'manca_cv', { mail: true, nome: false }).azione).toBe('cv')
  })

  it('fills the untouched mail from the state and keeps a destination picked before it', () => {
    const blank: CampaignForm = { ...EMPTY_FORM, bottoneMeta: 'link', bottoneUrl: LUMA, azione: 'clic' }
    const filled = withTemplate(blank, TEMPLATE, 'manca_cv', { mail: false, nome: false, meta: true })
    expect(filled).toMatchObject({
      oggetto: TEMPLATE.oggetto,
      testo: TEMPLATE.testo,
      bottoneTesto: TEMPLATE.bottone_testo,
      bottoneMeta: 'link',
      bottoneUrl: LUMA,
      azione: 'clic',
    })
    const wizard = withTemplate({ ...EMPTY_FORM, bottoneMeta: 'wizard' }, TEMPLATE, 'manca_cv', { mail: false, nome: false, meta: true })
    expect([wizard.bottoneMeta, wizard.azione, wizard.oggetto]).toEqual(['wizard', 'cv', TEMPLATE.oggetto])
    expect(withTemplate(EMPTY_FORM, TEMPLATE, 'manca_cv', { mail: false, nome: false }).bottoneMeta).toBe('area')
  })
})

describe('what the page saves (REB-530)', () => {
  it('sends the trimmed address with «Un link» and no address key with any other', () => {
    expect(payloadOf({ ...LINK, bottoneUrl: ` ${LUMA} ` })).toMatchObject({ bottone_meta: 'link', bottone_url: LUMA, azione: 'clic' })
    const hub = payloadOf({ ...LINK, bottoneMeta: 'area', azione: 'entrato' })
    expect(JSON.stringify(hub)).not.toContain('bottone_url')
    const lista = payloadOf({ ...LINK, fonte: 'lista', segueId: 'c1' })
    expect(lista).toEqual({ nome: 'Nuova campagna', oggetto: 'o', testo: 't', bottone_testo: 'b', bottone_meta: 'link', bottone_url: LUMA })
  })

  it('reads a stored address back into the field, and an empty one as nothing typed', () => {
    const stored = { ...({} as Campaign), fonte: 'stato', bottone_meta: 'link', bottone_url: LUMA, azione: 'clic' } as Campaign
    expect(formFromCampaign(stored).bottoneUrl).toBe(LUMA)
    expect(formFromCampaign({ ...stored, bottone_meta: 'area', bottone_url: null }).bottoneUrl).toBe('')
  })
})
