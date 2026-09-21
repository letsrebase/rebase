import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, use, useEffect, useRef, type ReactNode } from 'react'
import { forgetSession, identifySession } from './analytics'
import { api, unwrap } from './api'
import type { components } from './api-types'
import { queryKeys } from './query'
import { tenantPrefix } from './tenant'

export type SessionUser = components['schemas']['UserRead']

interface AuthValue {
  user: SessionUser | null
  isLoading: boolean
  login: (email: string, password: string) => Promise<void>
  /** Spends a link-by-mail token (`/app/verify?t=...`) and publishes the session. */
  enterWithLink: (t: string) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.me,
    queryFn: async () => {
      try {
        return await unwrap(api.GET('/api/auth/me'))
      } catch {
        return null // not authenticated is a normal state, not an error
      }
    },
    retry: false,
    // Without this, a role change or deactivation made from another session
    // only reaches an already-open tab on its next full reload: `useIsAdmin`/
    // `useCanWrite` read this same cached value, so a demoted admin kept
    // every admin screen -- and kept being able to try admin actions, even
    // though the backend's own `require_admin` would refuse them -- until
    // something forced a refetch. Polling only while a session actually
    // exists avoids hammering this on the login page before anyone has
    // authenticated.
    refetchInterval: (query) => (query.state.data ? 30_000 : false),
  })

  const loginMutation = useMutation({
    mutationFn: (body: { email: string; password: string }) =>
      unwrap(api.POST('/api/auth/login', { body })),
    onSuccess: (user) => queryClient.setQueryData(queryKeys.me, user),
  })

  const enterMutation = useMutation({
    mutationFn: (body: { t: string }) => unwrap(api.POST('/api/auth/verify', { body })),
    onSuccess: (user) => queryClient.setQueryData(queryKeys.me, user),
  })

  const logoutMutation = useMutation({
    mutationFn: () => unwrap(api.POST('/api/auth/logout')),
  })

  const user = data ?? null

  // The effect keys are the properties PostHog receives: a role change re-identifies,
  // a new object from the thirty-second poll does not. `identified` remembers who, so a
  // session that vanishes without a logout here (another tab logged out, a
  // deactivation, a revoked refresh) is forgotten once, before the router shows the
  // login page to what would otherwise still be that person. An anonymous visitor's
  // first `null` is not a reset: that would break the merge of their login pageview
  // into the person they are about to become.
  const identified = useRef<string | undefined>(undefined)
  const forget = () => {
    identified.current = undefined
    forgetSession()
  }
  const userId = user?.id
  const email = user?.email
  const nome = user?.nome
  const ruolo = user?.ruolo
  useEffect(() => {
    if (userId === undefined || email === undefined || nome === undefined || ruolo === undefined) {
      if (identified.current !== undefined) forget()
      return
    }
    identified.current = userId
    identifySession({ id: userId, email, nome, ruolo })
  }, [userId, email, nome, ruolo])

  const value: AuthValue = {
    user,
    isLoading,
    login: async (email, password) => {
      await loginMutation.mutateAsync({ email, password })
    },
    enterWithLink: async (t) => {
      await enterMutation.mutateAsync({ t })
    },
    /**
     * Ends the session and then leaves the page, whatever the server answered.
     *
     * The `POST` is what kills the session server-side and clears the cookies; the
     * navigation after it is a full page load of the login screen, not a router push.
     * A push would leave this tab's whole in-memory state alive -- the query cache, the
     * cross-tab refresh lock, every component that read `user` a moment ago -- and it
     * used to leave the person on the login form with `me` refetching behind it, which
     * is how a session the server had not fully dropped bounced them back in. A load
     * starts from nothing: the login page asks `me`, gets a 401, shows the form.
     *
     * `finally`, not `onSuccess`: a logout the network lost is still a logout the
     * person asked for, and the worst case of leaving anyway is the login page finding
     * the session alive and sending them back to the home.
     */
    logout: async () => {
      try {
        await logoutMutation.mutateAsync()
      } finally {
        // Before the page leaves, and in the `finally` for the same reason the
        // navigation is: the person asked to be forgotten here too, and the next login
        // on this browser must not be stitched onto them. Leaving matters more than
        // forgetting, so a third-party call that throws cannot keep the page here.
        try {
          forget()
        } catch {
          // deliberately ignored
        }
        queryClient.clear()
        window.location.assign(`${tenantPrefix}/app/login`)
      }
    },
  }

  return <AuthContext value={value}>{children}</AuthContext>
}

export function useAuth(): AuthValue {
  const value = use(AuthContext)
  if (!value) throw new Error('useAuth deve essere usato dentro AuthProvider')
  return value
}

export function useCanWrite(): boolean {
  const { user } = useAuth()
  return user?.ruolo === 'admin' || user?.ruolo === 'collaboratore'
}

export function useIsAdmin(): boolean {
  return useAuth().user?.ruolo === 'admin'
}
