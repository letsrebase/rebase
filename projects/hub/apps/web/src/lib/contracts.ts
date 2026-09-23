/**
 * The drafts the matches pages edit (REB-387): an input always holds a string, the API
 * takes typed values, and these helpers are the one place the two meet. Kept out of the
 * page files, which export components only (`react-refresh/only-export-components`).
 */
import type { Fiscal, FiscalData } from './api'

export type FiscalDraft = Record<keyof FiscalData, string>

export const FISCAL_EMPTY: FiscalDraft = { codice_fiscale: '', partita_iva: '', domicilio: '', pec: '' }

export function draftFromFiscal(fiscal: Fiscal | null): FiscalDraft {
  if (fiscal === null) return FISCAL_EMPTY
  return {
    codice_fiscale: fiscal.codice_fiscale,
    partita_iva: fiscal.partita_iva,
    domicilio: fiscal.domicilio,
    pec: fiscal.pec ?? '',
  }
}

/** An empty PEC is `null`, not `""`, which the API would try to read as an address. */
export function toFiscalData(draft: FiscalDraft): FiscalData {
  return {
    codice_fiscale: draft.codice_fiscale.trim(),
    partita_iva: draft.partita_iva.trim(),
    domicilio: draft.domicilio.trim(),
    pec: draft.pec.trim() || null,
  }
}
