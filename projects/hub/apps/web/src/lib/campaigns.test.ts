import { describe, expect, it } from 'vitest'

import {
  AZIONE_FATTA_LABELS,
  AZIONE_LABELS,
  CAMPAIGN_STATE_LABELS,
  META_LABELS,
  RECIPIENT_STATE_LABELS,
  campaignMoment,
  campaignStateLabel,
  defaultSchedule,
  isStalled,
  listRefetchEvery,
  oneLine,
  outcomeLine,
  peopleLabel,
  personalise,
  refetchEvery,
  romeTime,
  romeToday,
  scheduleLabel,
  share,
  stallLine,
  subjectFor,
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

describe('campaignMoment', () => {
  it('says when a scheduled or sending campaign leaves, in Rome time', () => {
    const at = { programmata_per: '2026-09-26T07:30:00Z', inviata_at: null }
    expect(campaignMoment({ ...at, stato: 'programmata' })).toBe('Parte il 26 settembre 2026 alle 09:30 (ora di Roma)')
    expect(campaignMoment({ ...at, stato: 'in_invio' })).toBe('Parte il 26 settembre 2026 alle 09:30 (ora di Roma)')
  })

  it('says when a sent campaign left, in Rome time, across the winter change too', () => {
    expect(campaignMoment({ stato: 'inviata', programmata_per: '2026-09-25T07:30:00Z', inviata_at: '2026-09-25T07:32:00Z' })).toBe(
      'Inviata il 25 settembre 2026 alle 09:32',
    )
    expect(campaignMoment({ stato: 'inviata', programmata_per: null, inviata_at: '2026-12-01T23:30:00Z' })).toBe(
      'Inviata il 2 dicembre 2026 alle 00:30',
    )
  })

  it('says nothing for a draft or a campaign cancelled before it left', () => {
    expect(campaignMoment({ stato: 'bozza', programmata_per: null, inviata_at: null })).toBeNull()
    expect(campaignMoment({ stato: 'annullata', programmata_per: '2026-09-26T07:30:00Z', inviata_at: null })).toBeNull()
  })
})

describe('a stopped send (REB-524)', () => {
  const stopped = { stato: 'in_invio' as const, fermo_at: '2026-09-28T09:32:00Z', fermo_motivo: 'Resend rifiuta l’invio: controlla la chiave e il dominio del mittente' }

  it('reads «Invio fermo» with its reason and since when, in Rome time, in place of «In invio»', () => {
    expect(isStalled(stopped)).toBe(true)
    expect(campaignStateLabel(stopped)).toBe('Invio fermo')
    expect(stallLine(stopped)).toBe(
      'Invio fermo: Resend rifiuta l’invio: controlla la chiave e il dominio del mittente, dal 28 settembre 2026 alle 11:32 (ora di Roma). Si riprova ogni minuto e riparte da solo appena è risolto.',
    )
  })

  it('is nothing once the send moves again, or on a campaign no longer sending', () => {
    const moving = { ...stopped, fermo_at: null, fermo_motivo: null }
    expect(isStalled(moving)).toBe(false)
    expect(campaignStateLabel(moving)).toBe('In invio')
    expect(stallLine(moving)).toBeNull()
    expect(isStalled({ ...stopped, stato: 'annullata' })).toBe(false)
    expect(campaignStateLabel({ ...stopped, stato: 'annullata' })).toBe('Annullata')
  })
})

describe('the subject on one line, as render.py sends it (REB-524)', () => {
  it('turns each run of control characters into one space and trims the ends', () => {
    expect(oneLine('Ada\r\nBcc: x@y')).toBe('Ada Bcc: x@y')
    expect(oneLine('\tCiao\u0085mondo\u2028!\n')).toBe('Ciao mondo !')
    expect(oneLine('Già pulito')).toBe('Già pulito')
  })

  it('puts the name in on one line, and a name that is only a newline counts as missing', () => {
    expect(subjectFor('{nome}, manca solo il CV', 'Ada\r\nBcc: x@y')).toBe('Ada Bcc: x@y, manca solo il CV')
    expect(subjectFor('Ciao {nome}!', '\n')).toBe('Ciao!')
    expect(subjectFor('Ciao {nome}!', null)).toBe('Ciao!')
    expect(subjectFor('Manca solo il CV\t', 'Ada')).toBe('Manca solo il CV')
  })
})

describe('listRefetchEvery', () => {
  it('polls «Campagne» every 30 s while a campaign is scheduled or sending, and never otherwise', () => {
    expect(listRefetchEvery([{ stato: 'inviata' }, { stato: 'in_invio' }])).toBe(30_000)
    expect(listRefetchEvery([{ stato: 'programmata' }])).toBe(30_000)
    expect(listRefetchEvery([{ stato: 'bozza' }, { stato: 'inviata' }, { stato: 'annullata' }])).toBe(false)
    expect(listRefetchEvery([])).toBe(false)
  })
})

describe('refetchEvery', () => {
  const now = Date.parse('2026-09-25T08:00:00Z')

  it('polls while the campaign is scheduled or sending', () => {
    expect(refetchEvery({ stato: 'programmata', inviata_at: null }, now)).toBe(10_000)
    expect(refetchEvery({ stato: 'in_invio', inviata_at: null }, now)).toBe(10_000)
  })

  it('keeps polling for five minutes after the send, while deliveries come in, then stops', () => {
    expect(refetchEvery({ stato: 'inviata', inviata_at: '2026-09-25T07:56:00Z' }, now)).toBe(10_000)
    expect(refetchEvery({ stato: 'inviata', inviata_at: '2026-09-25T07:54:59Z' }, now)).toBe(false)
  })

  it('never polls a draft or a cancelled campaign', () => {
    expect(refetchEvery({ stato: 'bozza', inviata_at: null }, now)).toBe(false)
    expect(refetchEvery({ stato: 'annullata', inviata_at: null }, now)).toBe(false)
  })
})

describe('the send button\'s words', () => {
  it('counts one person in the singular and the rest in the plural', () => {
    expect(peopleLabel(1)).toBe('1 persona')
    expect(peopleLabel(0)).toBe('0 persone')
    expect(peopleLabel(83)).toBe('83 persone')
  })

  it('says a Rome day and time as typed, whatever the browser\'s zone', () => {
    expect(scheduleLabel('2026-09-28', '09:00')).toBe('lun 28 set, 09:00')
    expect(scheduleLabel('2026-10-25', '02:30')).toBe('dom 25 ott, 02:30')
  })

  it('reads a stored moment as the time in Rome', () => {
    expect(romeTime('2026-09-26T08:32:00Z')).toBe('10:32')
    expect(romeTime('2026-12-01T08:32:00Z')).toBe('09:32')
  })
})

describe('the outcome in words', () => {
  it('gives a share of the mails sent, and nothing while none has left', () => {
    expect(share(3, 8)).toBe('38%')
    expect(share(0, 8)).toBe('0%')
    expect(share(2, 0)).toBeUndefined()
  })

  it('names every action once it is done', () => {
    expect(Object.keys(AZIONE_FATTA_LABELS).sort()).toEqual(Object.keys(AZIONE_LABELS).sort())
  })

  it('says what left and what it led to, and what went wrong only when something did', () => {
    const counts = { destinatari: 10, in_coda: 0, inviate: 8, saltate: 1, fallite: 0, consegnate: 8, rimbalzate: 0, cliccate: 4, entrate: 3, azioni: 2 }
    expect(outcomeLine(counts, 'cv')).toBe('8 inviate · 8 consegnate · 4 clic · 3 entrati · 2 CV caricati · 1 saltate')
    expect(outcomeLine({ ...counts, saltate: 0 }, 'entrato')).toBe('8 inviate · 8 consegnate · 4 clic · 3 entrati')
  })
})

describe('«Un link» (REB-530)', () => {
  it('offers the link as a destination and names the click as its action', () => {
    expect(META_LABELS.link).toBe('Un link')
    expect(AZIONE_LABELS.clic).toBe('Ha cliccato il link')
    expect(AZIONE_FATTA_LABELS.clic).toBe('Hanno cliccato il link')
  })

  it('says the click once, since for a link it is the action', () => {
    const counts = { destinatari: 10, in_coda: 0, inviate: 8, saltate: 0, fallite: 0, consegnate: 8, rimbalzate: 0, cliccate: 4, entrate: 1, azioni: 4 }
    expect(outcomeLine(counts, 'clic')).toBe('8 inviate · 8 consegnate · 4 clic · 1 entrati')
  })
})
