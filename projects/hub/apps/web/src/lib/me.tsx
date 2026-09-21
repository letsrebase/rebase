import { resetUser } from '@rebase/analytics/browser'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  member,
  type CompanyRequest,
  type CompanyUpdate,
  type FreelancerApplication,
  type Me,
  type MemberUpdate,
} from './api'
import { linkedinFieldValue, linkedinProfile } from './linkedin'

/** REB-279: `lib/auth.tsx`'s `useAdmin` and `lib/member.tsx`'s `useMember` merge into
 *  `useMe`, and their two logouts merge into one `useLogout`, both backed by the one
 *  identity route (`GET /api/hub/me`, `POST /api/hub/me/logout`). Everything else this
 *  module carries -- the magic link, the profile edit, the shapes the wizard's own
 *  fields read and write -- is unchanged from what `lib/member.tsx` used to hold. */
export const ME_KEY = ['me'] as const

/** Whoever `orbiters_user` resolves to, member or admin, or `null` signed out. A 401 is
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
  return useMutation({ mutationFn: (email: string) => member.requestLink(email) })
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

export function useUpdateCompany() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (data: CompanyUpdate) => member.updateCompany(data),
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

/** The one logout for anyone signed in, admin or not: always back to `/accedi`, since
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
 *  show as empty and its rules refuse. Only meaningful when `ha_scheda` is true; the
 *  caller checks that first (`Area.tsx`, `Modifica.tsx`). */
export function toApplication(me: Me): FreelancerApplication {
  return {
    nome: me.nome,
    cognome: me.cognome,
    email: me.email,
    linkedin_url: linkedinFieldValue(me.linkedin_url ?? ''),
    tariffa_giornaliera: me.tariffa_giornaliera ?? '',
    posizione: me.posizione ?? '',
    remoto: me.remoto ?? '',
    links: me.links,
    cv: null,
  }
}

/** What `PATCH /me` takes, trimmed the way the wizard trims before posting. */
export function toUpdate(value: FreelancerApplication): MemberUpdate {
  return {
    nome: value.nome.trim(),
    cognome: value.cognome.trim(),
    linkedin_url: linkedinProfile(value.linkedin_url) || null,
    tariffa_giornaliera: value.tariffa_giornaliera.replace(',', '.').trim(),
    posizione: value.posizione.trim(),
    remoto: value.remoto as MemberUpdate['remoto'],
    links: value.links.map((link) => link.trim()).filter(Boolean),
  }
}

/** The most recent request in the wizard's own shape, so `COMPANY_FIELDS`' `progetto`/
 *  `periodo_da`/`budget_giornaliero` entries can render and validate it exactly as
 *  they do in `CompanyWizard`. The company's own identity (`nome_azienda`, the
 *  referente) is never part of self-edit (REB-314) and is left blank here -- those
 *  three fields never read it. Only meaningful when `ha_azienda` is true; the caller
 *  checks that first (`Area.tsx`, `ModificaAzienda.tsx`). */
export function toCompanyApplication(me: Me): CompanyRequest {
  return {
    nome_azienda: '',
    referente_nome: '',
    referente_cognome: '',
    email: '',
    progetto: me.progetto ?? '',
    periodo_da: me.periodo_da ?? '',
    durata: me.durata ?? '',
    budget_giornaliero: me.budget_giornaliero ?? '',
  }
}

/** What `PATCH /me/company` takes: the four project answers, trimmed the way the
 *  wizard trims before posting. */
export function toCompanyUpdate(value: CompanyRequest): CompanyUpdate {
  return {
    progetto: value.progetto.trim(),
    periodo_da: value.periodo_da,
    durata: value.durata.trim(),
    budget_giornaliero: value.budget_giornaliero.replace(',', '.').trim(),
  }
}
