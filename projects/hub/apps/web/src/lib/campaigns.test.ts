import { describe, expect, it } from 'vitest'

import {
  AZIONE_LABELS,
  CAMPAIGN_STATE_LABELS,
  META_LABELS,
  RECIPIENT_STATE_LABELS,
  defaultSchedule,
  personalise,
  romeToday,
} from './campaigns'

describe('personalise', () => {
  it('puts the name in, or leaves a clean greeting like the server', () => {
    expect(personalise('Ciao {nome}, come va?', 'Ada')).toBe('Ciao Ada, come va?')
    expect(personalise('Ciao {nome}, come va?', null)).toBe('Ciao, come va?')
  })

  it('replaces every occurrence when a name is given', () => {
    expect(personalise('{nome}! Bentornata, {nome}.', 'Grace')).toBe('Grace! Bentornata, Grace.')
  })

  it('drops a leading placeholder with no name, no leading space to eat', () => {
    expect(personalise('{nome}, ciao', null)).toBe(', ciao')
  })

  it('drops every " {nome}" and every remaining "{nome}" with no name', () => {
    expect(personalise('Ciao {nome}, a presto {nome}', null)).toBe('Ciao, a presto')
  })

  it('leaves a name that itself contains braces untouched by the fallback rule', () => {
    expect(personalise('Ciao {nome}!', '{nome}')).toBe('Ciao {nome}!')
  })

  it('treats an empty-string name the same as null, the server rule using truthiness', () => {
    expect(personalise('Ciao {nome}, come va?', '')).toBe('Ciao, come va?')
  })
})

describe('romeToday', () => {
  it('reads the CET date and time before the spring change', () => {
    // 2026-01-15 10:30 UTC = 11:30 in Rome (UTC+1, CET)
    expect(romeToday(new Date('2026-01-15T10:30:00Z'))).toEqual({ giorno: '2026-01-15', ora: '11:30' })
  })

  it('reads the CEST date and time after the spring change, across the DST jump', () => {
    // 2026-03-29 is Rome's spring-forward Sunday (02:00 -> 03:00 CEST).
    // 2026-03-29 23:30 UTC = 2026-03-30 01:30 in Rome (UTC+2, CEST).
    expect(romeToday(new Date('2026-03-29T23:30:00Z'))).toEqual({ giorno: '2026-03-30', ora: '01:30' })
  })

  it('rolls the day forward across midnight in Rome even while still the prior day in UTC', () => {
    // 2026-06-30 22:15 UTC = 2026-07-01 00:15 in Rome (UTC+2, CEST).
    expect(romeToday(new Date('2026-06-30T22:15:00Z'))).toEqual({ giorno: '2026-07-01', ora: '00:15' })
  })
})

describe('defaultSchedule', () => {
  it('proposes an hour from now, rounded up to the next quarter hour, in Rome time', () => {
    // 07:07 UTC = 09:07 in Rome (CEST); + 1 h = 10:07; next quarter = 10:15.
    expect(defaultSchedule(new Date('2026-09-25T07:07:00Z'))).toEqual({ giorno: '2026-09-25', ora: '10:15' })
  })

  it('keeps a time already on a quarter hour', () => {
    // 08:30 UTC = 09:30 in Rome (CET) + 1 h = 10:30.
    expect(defaultSchedule(new Date('2026-01-15T08:30:00Z'))).toEqual({ giorno: '2026-01-15', ora: '10:30' })
  })

  it('rolls over to the next Rome day late in the evening', () => {
    // 21:50 UTC = 23:50 in Rome (CEST); + 1 h = 00:50; next quarter = 01:00 the day after.
    expect(defaultSchedule(new Date('2026-09-25T21:50:00Z'))).toEqual({ giorno: '2026-09-26', ora: '01:00' })
  })
})

describe('labels', () => {
  it('labels every campaign state in Italian', () => {
    expect(CAMPAIGN_STATE_LABELS.bozza).toBe('Bozza')
    expect(CAMPAIGN_STATE_LABELS.programmata).toBe('Programmata')
    expect(CAMPAIGN_STATE_LABELS.in_invio).toBe('In invio')
    expect(CAMPAIGN_STATE_LABELS.inviata).toBe('Inviata')
    expect(CAMPAIGN_STATE_LABELS.annullata).toBe('Annullata')
  })

  it('labels every action in Italian', () => {
    expect(AZIONE_LABELS.entrato).toBe('È entrato nell’area')
    expect(AZIONE_LABELS.cv).toBe('Ha caricato il CV')
    expect(AZIONE_LABELS.scheda_completa).toBe('Ha completato la scheda')
    expect(AZIONE_LABELS.profilo_creato).toBe('Ha creato il profilo')
    expect(AZIONE_LABELS.richiesta_aggiornata).toBe('Ha aggiornato la richiesta')
    expect(AZIONE_LABELS.pigro_cliente).toBe('Primo cliente in Pigro')
  })

  it('offers only the three phase-1 button destinations', () => {
    expect(META_LABELS.area).toBe('La sua area')
    expect(META_LABELS.wizard).toBe('Il wizard del profilo')
    expect(META_LABELS.richiesta).toBe('La richiesta dell’azienda')
    expect(META_LABELS.pigro).toBeUndefined()
  })

  it('labels every recipient state in Italian', () => {
    expect(RECIPIENT_STATE_LABELS.in_coda).toBe('In coda')
    expect(RECIPIENT_STATE_LABELS.inviata).toBe('Inviata')
    expect(RECIPIENT_STATE_LABELS.saltata).toBe('Saltata')
    expect(RECIPIENT_STATE_LABELS.fallita).toBe('Fallita')
  })
})
