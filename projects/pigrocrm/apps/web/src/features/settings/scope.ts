/** The words and the body of a member's scope (REB-635, spec 2026-10-03 §1.11, §5
 *  Team), kept out of `UsersPanel.tsx` so the component file exports components alone. */
import type { AziendaRecord } from '@/lib/azienda'

/** What a row says about a person's scope (spec 2026-10-03 §5 Team, REB-635): «Tutte»
 *  for the whole space, the names of their aziende, or that none of them is active any
 *  more, which the server never widens back to «tutte» (§1.11). */
export function scopeLabel(
  aziende: string[] | null | undefined,
  byId: (id: string) => AziendaRecord | undefined,
): string {
  if (!Array.isArray(aziende)) return 'Tutte'
  const names = aziende.map((id) => byId(id)?.nome).filter((nome): nome is string => !!nome)
  return names.length > 0 ? names.join(', ') : 'nessuna azienda attiva'
}

/** The body an invite or an update sends for a set of checked aziende: `null`, the
 *  whole space, when every active azienda is checked, since a person who sees them all
 *  today should see the next one too; the ids otherwise. */
export function scopeBody(checked: string[], every: AziendaRecord[]): string[] | null {
  return every.every((a) => checked.includes(a.id)) ? null : checked
}
