import type { UserRecord } from './queries'

/** The roles an admin may give, in the words the product writes (the API stores the enum). */
export const ROLES: { value: UserRecord['role']; label: string }[] = [
  { value: 'admin', label: 'Amministratore' },
  { value: 'collaborator', label: 'Collaboratore' },
  { value: 'readonly', label: 'Sola lettura' },
]
