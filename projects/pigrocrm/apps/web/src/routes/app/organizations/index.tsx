import { createFileRoute, redirect } from '@tanstack/react-router'
import {
  REGISTRY_PAGE_PATHS,
  legacyRegistryPage,
  parseRegistrySearch,
} from '@/features/organizations/search'

/**
 * Old path support (REB-1053): `/app/organizations` was the one «Anagrafiche» page, with the
 * view in `?role=`. «Clienti» and «Fornitori» have a page each now, so a bookmark or a link
 * already sent lands on the one it meant: `?role=supplier` on «Fornitori», anything else (the
 * retired «Tutte» tab included) on «Clienti». The `?search=` term is carried over. The
 * organization's own page, `$organizationId.tsx`, is still here.
 */
export const Route = createFileRoute('/app/organizations/')({
  validateSearch: (search: Record<string, unknown>) => ({
    ...parseRegistrySearch(search),
    role: legacyRegistryPage(search),
  }),
  beforeLoad: ({ search }) => {
    const { role, ...rest } = search
    throw redirect({ to: REGISTRY_PAGE_PATHS[role], search: rest })
  },
})
