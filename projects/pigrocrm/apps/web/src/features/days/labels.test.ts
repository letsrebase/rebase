import { describe, expect, it } from 'vitest'
import {
  ACTIONS,
  StatusLabels,
  STATUS_TONE,
  actorLabel,
  reasonLabel,
  activeRateCard,
  allowsFraction,
  cellDaysLabel,
  quantityLabel,
  statusLabel,
  statusTone,
  unitLabel,
} from './labels'
import { rateCard } from './testing'

describe('quantityLabel', () => {
  it('says a whole day and half a day in words, and any other size in days', () => {
    expect(quantityLabel('1.00')).toBe('Giornata intera')
    expect(quantityLabel('0.50')).toBe('Mezza giornata')
    expect(quantityLabel('1.50')).toBe('1,5 giornate')
    expect(quantityLabel('2.00')).toBe('2 giornate')
  })
})

describe('the ledger’s states', () => {
  it('has a label and a tone for every state, and tells a worked-without-approval day from a worked one', () => {
    for (const status of Object.keys(StatusLabels)) {
      expect(statusLabel(status)).toBe(StatusLabels[status as keyof typeof StatusLabels])
      expect(statusTone(status)).toBe(STATUS_TONE[status as keyof typeof STATUS_TONE])
    }
    expect(statusLabel('worked_without_approval')).toBe('Lavorata senza approvazione')
    expect(statusTone('worked_without_approval')).toBe('gold')
    expect(statusTone('worked')).toBe('ink')
  })

  it('shows a state it does not know as it came, in a quiet tone', () => {
    expect(statusLabel('sospeso')).toBe('sospeso')
    expect(statusTone('sospeso')).toBe('muted')
  })

  it('asks for a reason on the moves that are a judgement and not on the ones that are the act', () => {
    expect(ACTIONS.disputed?.defaultReason).toBeNull()
    expect(ACTIONS.revoked?.defaultReason).toBeNull()
    expect(ACTIONS.worked?.defaultReason).toBe('Giornata svolta')
  })

  it('names who made a move', () => {
    expect(actorLabel({ kind: 'human', id: 'u1' })).toBe('Una persona')
    expect(actorLabel({ kind: 'agent', id: 'u1' })).toBe('Un assistente')
    expect(actorLabel({ kind: 'system' })).toBe('Il sistema')
  })
})

describe('the rate in force', () => {
  const closed = rateCard({ id: 'old', valid_from: '2026-01-01', valid_to: '2026-06-30', amount: '400.00' })
  const open = rateCard({ id: 'new', valid_from: '2026-07-01', valid_to: null, amount: '450.00' })

  it('is the card whose range contains the date, ends included', () => {
    expect(activeRateCard([closed, open], '2026-06-30')?.id).toBe('old')
    expect(activeRateCard([closed, open], '2026-07-01')?.id).toBe('new')
    expect(activeRateCard([closed, open], '2027-01-01')?.id).toBe('new')
    expect(activeRateCard([closed, open], '2025-12-31')).toBeNull()
  })

  it('allows a fraction the card lists, compared as numbers and not as strings', () => {
    expect(allowsFraction(rateCard({ allowed_fractions: ['1.00', '0.5'] }), '0.50')).toBe(true)
    expect(allowsFraction(rateCard({ allowed_fractions: ['1.00'] }), '0.50')).toBe(false)
    expect(allowsFraction(null, '0.50')).toBe(true)
  })
})

describe('cellDaysLabel', () => {
  const day = (status: string) => ({
    id: status,
    contract_id: 'c',
    contract_title: 't',
    customer_name: 'n',
    quantity: '1.00',
    status,
  })
  it('counts the days and says how many are worked without approval', () => {
    expect(cellDaysLabel([])).toBe('')
    expect(cellDaysLabel([day('worked')])).toBe(', 1 giornata')
    expect(cellDaysLabel([day('worked'), day('worked_without_approval')])).toBe(
      ', 2 giornate, 1 senza approvazione',
    )
  })
})

describe('unitLabel', () => {
  it('says the unit as the words after a rate’s figure', () => {
    expect(unitLabel('day')).toBe('al giorno')
    expect(unitLabel('ora')).toBe('all’ora')
    expect(unitLabel('year')).toBe('all’anno')
    expect(unitLabel('flat')).toBe('a forfait')
    expect(unitLabel('week')).toBe('per settimana')
  })
})

describe('reasonLabel', () => {
  it('words the key the service writes when a day is recorded', () => {
    expect(reasonLabel('creation')).toBe('Giornata registrata')
  })

  it('shows a reason a person typed as they typed it', () => {
    expect(reasonLabel('Il cliente ha contestato le ore')).toBe('Il cliente ha contestato le ore')
  })

  it('shows a reason named like an object property as typed', () => {
    for (const reason of ['constructor', '__proto__', 'toString']) {
      expect(reasonLabel(reason)).toBe(reason)
    }
  })
})
