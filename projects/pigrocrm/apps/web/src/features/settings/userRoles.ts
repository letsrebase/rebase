import type { UserRecord } from './queries'

/** The roles an admin may give, in the words the product writes (the API stores the enum). */
export const ROLES: { value: UserRecord['ruolo']; label: string }[] = [
  { value: 'admin', label: 'Amministratore' },
  { value: 'collaboratore', label: 'Collaboratore' },
  { value: 'readonly', label: 'Sola lettura' },
]
