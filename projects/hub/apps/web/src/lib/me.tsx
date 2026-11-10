import { resetUser } from '@rebase/analytics/browser'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  member,
  type CompanyRequest,
  type CompanyUpdate,
  type ContactPointWrite,
  type FreelancerApplication,
  type Me,
  type MemberRequest,
  type MemberUpdate,
} from './api'
import { sentAmount } from './amount'
import { linkedinFieldValue, linkedinProfile } from './linkedin'
import type { Utm } from './utm'

/** REB-279: `lib/auth.tsx`'s `useAdmin` and `lib/member.tsx`'s `useMember` merge into
 *  `useMe`, and their two logouts merge into one `useLogout`, both backed by the one
 *  identity route (`GET /api/hub/me`, `POST /api/hub/me/logout`). Everything else this
 *  module carries -- the magic link, the profile edit, the shapes the wizard's own
 *  fields read and write -- is unchanged from what `lib/member.tsx` used to hold. */
export const ME_KEY = ['me'] as const

/** Whoever `rebase_user` resolves to, member or admin, or `null` signed out. A 401 is
 *  "not signed in", not an error. */
export function useMe() {
  return useQuery({
    queryKey: ME_KEY,
    queryFn: async (): Promise<Me | null> => {
      try {
        return await member.me()
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null
        throw error
      }
    },
    retry: false,
    staleTime: 60_000,
  })
}

export function useRequestLink() {
  return useMutation({
    mutationFn: ({ email, utm }: { email: string; utm?: Utm }) => member.requestLink(email, utm),
  })
}

export function useEnter() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (token: string) => member.enter(token),
    onSuccess: (me) => client.setQueryData(ME_KEY, me),
  })
}

export function useUpdateProfile() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (data: MemberUpdate) => member.update(data),
    onSuccess: (me) => client.setQueryData(ME_KEY, me),
  })
}

export function useUpdateContactPoints() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (points: ContactPointWrite[]) => member.updateContactPoints(points),
    onSuccess: (me) => client.setQueryData(ME_KEY, me),
  })
}

export function useUpdateCompany() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: CompanyUpdate }) => member.updateCompany(id, data),
    onSuccess: (me) => client.setQueryData(ME_KEY, me),
  })
}

/** A brand-new request instead of an edit (REB-381): same shape, different route. */
export function useCreateCompanyRequest() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (data: CompanyUpdate) => member.createCompanyRequest(data),
    onSuccess: (me) => client.setQueryData(ME_KEY, me),
  })
}

export function useReplaceCv() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => member.replaceCv(file),
    onSuccess: (me) => client.setQueryData(ME_KEY, me),
  })
}

/** The one logout for anyone signed in, admin or not: always back to `/login`, since
 *  an admin's own way in is the same magic link now and there is no separate admin
 *  door to send them to. */
export function useLogout() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => member.logout(),
    onSettled: () => {
      resetUser()
      client.clear()
      window.location.assign('/hub/login')
    },
  })
}

/** The profile in the wizard's own shape, so its steps can render and validate it.
 *  `cv` is `null`: the file we hold is not a `File` in the browser. The answers an
 *  incomplete card lacks (ORB-155) become `''`, which is what the wizard's fields
 *  show as empty and its rules refuse. Only meaningful when `has_card` is true; the
 *  caller checks that first (`Area.tsx`, `Edit.tsx`). */
export function toApplication(me: Me): FreelancerApplication {
  return {
    first_name: me.first_name,
    last_name: me.last_name,
    email: me.email,
    linkedin_url: linkedinFieldValue(me.linkedin_url ?? ''),
    daily_rate: me.daily_rate ?? '',
    job_title: me.job_title ?? '',
    remote: me.remote ?? '',
    links: me.links,
    cv: null,
  }
}

/** What `PATCH /me` takes, trimmed the way the wizard trims before posting. */
export function toUpdate(value: FreelancerApplication): MemberUpdate {
  return {
    first_name: value.first_name.trim(),
    last_name: value.last_name.trim(),
    linkedin_url: linkedinProfile(value.linkedin_url) || null,
    daily_rate: sentAmount(value.daily_rate),
    job_title: value.job_title.trim(),
    remote: value.remote as MemberUpdate['remote'],
    links: value.links.map((link) => link.trim()).filter(Boolean),
  }
}

/** One request in the wizard's own shape, so `COMPANY_FIELDS`' editable entries can
 *  render, validate and summarise it exactly as they do in `CompanyWizard` (REB-314;
 *  REB-380 adds `remote`/`onsite_days`/`headcount`/`requested_role`). The
 *  company's own identity (`company_name`, the referente, `phone`) is never part of
 *  self-edit and is left blank here -- those fields never read it. */
export function toCompanyApplication(request: MemberRequest): CompanyRequest {
  return {
    company_name: '',
    requested_role: request.requested_role,
    contact_first_name: '',
    contact_last_name: '',
    email: '',
    phone: '',
    project: request.project,
    period_from: request.period_from,
    duration: request.duration,
    daily_budget: request.daily_budget,
    remote: request.remote,
    onsite_days: request.onsite_days != null ? String(request.onsite_days) : '',
    headcount: String(request.headcount),
  }
}

/** What `PATCH /me/company` and, since REB-381, `POST /me/company` both take: the
 *  eight project answers, trimmed the way the wizard trims before posting;
 *  `onsite_days` blank means "not ibrido", the same null-when-empty conversion
 *  `requestPeople` makes on the public wizard's side. `EditCompany.tsx` sends it
 *  to the `PATCH`, `NewCompanyRequest.tsx` to the `POST` -- the payload shape is
 *  identical, only the route differs. */
export function toCompanyUpdate(value: CompanyRequest): CompanyUpdate {
  return {
    project: value.project.trim(),
    period_from: value.period_from,
    duration: value.duration.trim(),
    daily_budget: sentAmount(value.daily_budget),
    remote: value.remote as CompanyUpdate['remote'],
    onsite_days: value.onsite_days ? Number(value.onsite_days) : null,
    headcount: Number(value.headcount),
    requested_role: value.requested_role.trim(),
  }
}
