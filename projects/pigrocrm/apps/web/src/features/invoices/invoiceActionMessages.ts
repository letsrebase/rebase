import { isMissingFile, type ProblemDetail } from '@/lib/api'

/**
 * What the bar says under an issued invoice whose PDF or XML does not exist, or `null`
 * when both do. The pointer at «Rigenera documenti» follows the role: a person who cannot
 * press it is told where the file comes from rather than to press a button they do not
 * have (REB-294 hides it from a readonly role).
 */
export function missingFilesSentence(
  hasPdf: boolean,
  hasXml: boolean,
  mayProduce: boolean,
): string | null {
  if (hasPdf && hasXml) return null
  const both = !hasPdf && !hasXml
  const what = both
    ? 'Il PDF e l’XML FatturaPA di questa fattura non sono stati generati.'
    : hasPdf
      ? 'L’XML FatturaPA di questa fattura non è stato generato.'
      : 'Il PDF di questa fattura non è stato generato.'
  const how = mayProduce
    ? `Premi «Rigenera documenti» per ${both ? 'generarli' : 'generarlo'}: il numero resta quello.`
    : `${both ? 'Si generano' : 'Si genera'} con «Rigenera documenti», che il tuo ruolo non può usare.`
  return `${what} ${how}`
}

/**
 * The toast for a failed download. A 404 here means the row points at no file, at a
 * document with no current version, or at a stored file that is gone, and the server's
 * detail for each is written for a log: `invoice_artifact <uuid>#xml not found` (REB-168,
 * seen on production 2026-09-11), `document_blob <key> not found`. It becomes a sentence
 * naming the file, and «Rigenera documenti» when that button is on the page: it produces a
 * missing file and repairs a lost one with identical bytes (`produce_artifacts`). Keyed
 * on the status via `isMissingFile` (`lib/api.ts`), rather than on the entity, so a lost
 * `document_blob` gets the same sentence. Any other failure keeps the server's `detail`
 * as every other action on this bar does.
 */
export function downloadErrorSentence(
  problem: ProblemDetail,
  kind: 'pdf' | 'xml',
  noun: 'fattura' | 'proforma',
  canRegenerate: boolean,
): string {
  if (!isMissingFile(problem)) return problem.detail
  const file = kind === 'xml' ? 'L’XML FatturaPA' : 'Il PDF'
  const retry = canRegenerate ? ' Premi «Rigenera documenti» per generarlo di nuovo.' : ''
  return `${file} di questa ${noun} non è disponibile.${retry}`
}
