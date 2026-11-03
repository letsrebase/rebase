import type { SessionUser } from './auth'

/**
 * A role as the product writes it: «admin» is the value the API stores, not a word that
 * belongs on a screen (spec §5.5 — everything the user reads is Italian).
 *
 * Lower case, because both readers put it inside a sentence or under a name rather than
 * at the head of one; `features/settings/UsersPanel.tsx` keeps its own capitalised set
 * for the options of a `<Select>`, which is a list of titles and not prose.
 *
 * Its own module and not `lib/auth.tsx`: every test of a component that shows a role
 * mocks `@/lib/auth` wholesale, and a constant living there would come back undefined in
 * all of them.
 */
export const ROLE_LABELS: Record<SessionUser['role'], string> = {
  admin: 'amministratore',
  collaborator: 'collaborator',
  readonly: 'sola lettura',
}

/** The label for a stored role, or the stored value itself if one is ever added to the
 *  API before it is added here. */
export function roleLabel(role: string): string {
  return ROLE_LABELS[role as SessionUser['role']] ?? role
}
