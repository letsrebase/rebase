import { api } from './api'
import { tenantPrefix } from './tenant'

/** Where a fresh session goes after a one-time link is spent: the space's home, or the
 *  root's under its own name (the same rule as `login.tsx`). Shared by `/app/verify`
 *  (REB-229) and `/app/invite` (REB-291) because both land through it; asks
 *  `/api/tenants/root` only on the bare page. */
export async function homeAfterEntry(): Promise<string> {
  if (tenantPrefix !== '') return `${tenantPrefix}/app/`
  const { data } = await api.GET('/api/tenants/root')
  return data?.slug ? `/${data.slug}/app/` : '/app/'
}
