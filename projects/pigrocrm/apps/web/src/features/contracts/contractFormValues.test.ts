import { describe, expect, it } from 'vitest'
import {
  contractToFormValues,
  defaultContractFormValues,
  toContractBody,
} from './contractFormValues'
import type { Contract } from './queries'

const CONTRACT: Contract = {
  id: 'c1',
  customer_id: 'cust-1',
  azienda_id: 'a1',
  titolo: 'Sviluppo piattaforma',
  inizio: '2026-01-01',
  fine: '2026-12-31',
  tipo_rinnovo: 'tacito',
  preavviso_rinnovo_giorni: 60,
  preavviso_disdetta_giorni: 30,
  giorni_pagamento: 30,
  pagamento_fine_mese: true,
  cadenza_fatturazione: 'mensile',
  divisa: 'EUR',
  requires_prior_approval: true,
  applies_social_charge: false,
  politica_spese: { kind: 'rimborsabile_con_tetto', importo_tetto: '500.00' },
  stato: 'attivo',
  contratto_precedente_id: null,
  note: null,
  custom_fields: {},
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

const nothingRendered = () => false

describe('toContractBody', () => {
  it('sends every field on create, with the payment terms null when they follow the customer', () => {
    const values = {
      ...defaultContractFormValues(),
      titolo: '  Manutenzione  ',
      inizio: '2026-11-01',
    }
    expect(toContractBody(values, undefined, nothingRendered)).toMatchObject({
      titolo: 'Manutenzione',
      inizio: '2026-11-01',
      fine: null,
      tipo_rinnovo: 'nessuno',
      preavviso_rinnovo_giorni: null,
      preavviso_disdetta_giorni: 30,
      giorni_pagamento: null,
      pagamento_fine_mese: null,
      politica_spese: { kind: 'non_rimborsabile' },
    })
  })

  it('sends only the notice period when only the notice period changed', () => {
    const initial = contractToFormValues(CONTRACT)
    const body = toContractBody({ ...initial, preavviso_disdetta_giorni: '90' }, initial, nothingRendered)
    expect(body).toEqual({ preavviso_disdetta_giorni: 90 })
  })

  it('sends both halves of the payment terms when one moved', () => {
    const initial = contractToFormValues(CONTRACT)
    const body = toContractBody({ ...initial, giorni_pagamento: '60' }, initial, nothingRendered)
    expect(body).toEqual({ giorni_pagamento: 60, pagamento_fine_mese: true })
  })

  it('clears both payment terms when the contract goes back to the customer’s', () => {
    const initial = contractToFormValues(CONTRACT)
    const body = toContractBody({ ...initial, pagamento_proprio: false }, initial, nothingRendered)
    expect(body).toEqual({ giorni_pagamento: null, pagamento_fine_mese: null })
  })

  it('sends the renewal type with its notice, and drops the notice for «nessuno»', () => {
    const initial = contractToFormValues(CONTRACT)
    const body = toContractBody({ ...initial, tipo_rinnovo: 'nessuno' }, initial, nothingRendered)
    expect(body).toEqual({ tipo_rinnovo: 'nessuno', preavviso_rinnovo_giorni: null })
  })

  it('writes the expense policy as the tagged shape the trigger reads', () => {
    const initial = contractToFormValues(CONTRACT)
    const body = toContractBody(
      { ...initial, spese_kind: 'rimborsabile', spese_preautorizzazione: true },
      initial,
      nothingRendered,
    )
    expect(body).toEqual({
      politica_spese: { kind: 'rimborsabile', richiede_preautorizzazione: true },
    })
  })

  it('clears an emptied end date with null', () => {
    const initial = contractToFormValues(CONTRACT)
    expect(toContractBody({ ...initial, fine: '' }, initial, nothingRendered)).toEqual({ fine: null })
  })

  it('leaves a stored custom value whose definition is archived out of the body', () => {
    const initial = contractToFormValues({ ...CONTRACT, custom_fields: { vecchio: 'x' } })
    expect(toContractBody(initial, initial, nothingRendered)).toEqual({})
  })

  it('sends a cleared rendered custom field as null so the service removes it', () => {
    const initial = contractToFormValues({ ...CONTRACT, custom_fields: { referente: 'Rossi' } })
    const body = toContractBody(
      { ...initial, custom: { referente: '' } },
      initial,
      (key) => key === 'referente',
    )
    expect(body).toEqual({ custom_fields: { referente: null } })
  })
})

describe('toContractBody review fixes', () => {
  it('normalises a decimal comma in the cap', () => {
    const initial = contractToFormValues(CONTRACT)
    const body = toContractBody({ ...initial, spese_tetto: '600,50' }, initial, () => false)
    expect(body).toEqual({
      politica_spese: { kind: 'rimborsabile_con_tetto', richiede_preautorizzazione: false, importo_tetto: '600.50' },
    })
  })

  it('sends an emptied disdetta notice as null so the API answers', () => {
    const initial = contractToFormValues(CONTRACT)
    expect(
      toContractBody({ ...initial, preavviso_disdetta_giorni: '' }, initial, () => false),
    ).toEqual({ preavviso_disdetta_giorni: null })
  })

  it('does not resend a custom value nobody touched', () => {
    const initial = contractToFormValues({ ...CONTRACT, custom_fields: { referente: 'Rossi' } })
    expect(toContractBody(initial, initial, (key) => key === 'referente')).toEqual({})
  })
})
