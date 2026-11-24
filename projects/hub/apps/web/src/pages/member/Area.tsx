import { capture } from '@rebase/analytics/browser'
import { useQuery } from '@tanstack/react-query'
import { Link, useSearch } from '@tanstack/react-router'
import { ArrowUpRight, BadgeCheck, CircleX, Clock, Download, Pencil, Plus } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { formatMoney } from '@rebase/ui/format'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@rebase/ui/tabs'
import { member, type Me, type MemberRequest } from '@/lib/api'
import { REMOTE_LABELS, formatBytes, formatDate, formatDay } from '@/lib/format'
import { toApplication, toCompanyApplication, useMe } from '@/lib/me'
import { GUIDE } from '@/lib/perks'
import { keys } from '@/lib/query-keys'
import { COMPANY_FIELDS } from '@/pages/CompanyWizard'
import { LoadingState } from '@/pages/admin/list-kit'
import { FREELANCER_FIELDS } from '@/pages/FreelancerWizard'
import { MemberContacts } from '@/pages/member/Contacts'
import { MemberContracts } from '@/pages/member/Contracts'
import { MemberGithub } from '@/pages/member/Github'
import { MemberReferral } from '@/pages/member/Referral'
import { MemberWorkProfile } from '@/pages/member/WorkProfile'

// `/app/login`, not `/app/register`: PigroCRM's own login already knows what to do with
// whoever is behind it (REB-377's identity-cookie chooser, its own effect that sends an
// already-authenticated visitor straight to their dashboard, and REB-482's "Crea un nuovo spazio" beside the chooser too) and offers "Crea il tuo spazio" to anyone with
// nothing to open yet. `/app/register` used to sit here and always opened its own
// two-step form first, even to a member who already had a space -- its own hub-lookup
// only ran after that, one screen further in, and only then offered a link to enter
// instead of creating a second one.
const PIGROCRM_URL = 'https://pigro.letsrebase.com/app/login'
const ROLE_LABELS: Record<string, string> = { admin: 'Amministratore', member: 'Membro' }

type TabName = 'card' | 'profile' | 'requests' | 'contracts' | 'contacts'
type WorkProfile = NonNullable<Me['work_profile']>

/** What the wizard's own field says about one answer of a request (the hybrid option
 *  with its on-site days, the headcount with its noun), so the list and the edit form use
 *  the same words. */
function said(id: 'remote' | 'headcount', request: MemberRequest): string {
  const field = COMPANY_FIELDS.find((candidate) => candidate.id === id)
  return field?.summary(toCompanyApplication(request)) || '—'
}

function Cell({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="bg-card px-4 py-2.5 text-sm">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 break-words font-medium tabular-nums">{children}</dd>
    </div>
  )
}

/** One request, compact: the role and when it was filed, its own «Modifica», the project
 *  clipped to two lines, then the four answers that tell requests apart in a hairline
 *  grid. The whole text is on the edit page. */
function RequestCard({ request, position, total }: { request: MemberRequest; position: number; total: number }) {
  return (
    <li className="border bg-card">
      <div className="flex items-start justify-between gap-3 px-4 pt-3">
        <div className="min-w-0">
          <h3 className="break-words font-semibold">{request.requested_role}</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">Inviata il {formatDate(request.created_at)}</p>
        </div>
        <Button asChild variant="outline" size="sm" className="shrink-0">
          <Link
            to="/me/edit-company/$id"
            params={{ id: request.id }}
            aria-label={`Modifica la richiesta ${position} di ${total}: ${request.requested_role}`}
          >
            <Pencil className="mr-2 size-4" />
            Modifica
          </Link>
        </Button>
      </div>
      {/* The padding sits on the wrapper: on the clamped element itself the lines past the
          second would show through its bottom padding. */}
      <div className="px-4 pt-2 pb-3">
        <p className="line-clamp-2 text-sm text-muted-foreground">{request.project}</p>
      </div>
      <dl className="grid grid-cols-2 gap-(--line) border-t bg-border">
        <Cell label="Budget">{formatMoney(request.daily_budget)} / giorno</Cell>
        <Cell label="Da quando">
          {formatDay(request.period_from)}, {request.duration}
        </Cell>
        <Cell label="Modalità">{said('remote', request)}</Cell>
        <Cell label="Persone">{said('headcount', request)}</Cell>
      </dl>
    </li>
  )
}

/** «Profilo di lavoro in revisione», «… approvato»…: where the profile stands, in the
 *  header, with an icon beside the word, so it is read before any tab is opened. The words
 *  are the ones «Profilo di lavoro» itself uses. */
function ProfilePill({ profile }: { profile: WorkProfile }) {
  const [Icon, word] = profile.expired
    ? [Clock, 'scaduto']
    : ({ draft: [Clock, 'in revisione'], approved: [BadgeCheck, 'approvato'], rejected: [CircleX, 'non approvato'] } as const)[
        profile.state
      ]
  return (
    <Badge variant="pill" className="gap-1">
      <Icon aria-hidden="true" />
      Profilo di lavoro {word}
    </Badge>
  )
}

/** The tab strip, which scrolls sideways on a phone. The page may open on a tab past the
 *  strip's right edge (a document to sign opens «Contratti», the fourth), so the active
 *  tab is scrolled into the strip whenever it changes; only the strip moves, never the page. */
function TabBar({ active, children }: { active: string; children: ReactNode }) {
  const list = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const strip = list.current
    const tab = strip?.querySelector<HTMLElement>('[data-state="active"]')
    if (!strip || !tab) return
    const left = tab.getBoundingClientRect().left - strip.getBoundingClientRect().left + strip.scrollLeft
    if (left < strip.scrollLeft || left + tab.offsetWidth > strip.scrollLeft + strip.clientWidth) strip.scrollLeft = left
  }, [active])
  return (
    <div className="border-b border-border">
      <TabsList
        ref={list}
        variant="line"
        className="max-w-full justify-start overflow-x-auto overflow-y-hidden pb-px [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {children}
      </TabsList>
    </div>
  )
}

/** One perk of the rail's «Per chi è dentro», a hairline above every one but the first. */
function Perk({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="space-y-2 border-t pt-3 first-of-type:border-t-0 first-of-type:pt-0">
      <h3 className="font-semibold">{title}</h3>
      {children}
    </div>
  )
}

/** «La tua area» (REB-1171): a header with who this is, the facts a company searches by and
 *  the two actions on the card, then tabs on the left and a rail on the right, the shape of
 *  the admin's talent page (REB-1158), so the two pages that describe a person read alike.
 *
 *  The tabs are only the ones this person has: «La tua scheda» (the card's answers, the CV
 *  and GitHub) and «Profilo di lavoro» with a card, «Richieste» with a company request,
 *  «Contratti» with a card, «Recapiti» for everyone. With one tab there is no bar. The page
 *  opens on the tab that waits for the person: a document to sign, then an incomplete card,
 *  then a work profile to send (none, rejected or expired); otherwise the first tab. A
 *  return from GitHub (`?github=`) opens the card, where its notice is. That choice is made
 *  once, after the contracts have answered, and then kept: a signature or a profile sent
 *  while the page is open refetches the data, and the open tab must not move under the
 *  person. Every panel stays mounted and only the active one shows, so a half-written
 *  contact or a GitHub confirmation survives a look at another tab.
 *
 *  The rail holds what rebase gives back, on every tab: the referral link (REB-610) and,
 *  for a freelancer or an admin, the two perks (REB-385: never for a company-only contact,
 *  for whom PigroCRM and the guide mean nothing). Under `lg` it follows the tabs.
 *
 *  `useMe()` answers member and admin alike (REB-279), so a person may hold none, one or
 *  all of a card, a request and the admin role; the header names each with a badge
 *  (REB-601), «Membro» when none applies. This replaces the one-column stack of REB-641,
 *  which put up to nine sections in a 768 px column in the middle of a 1,270 px area.
 *
 *  The `denied` flag is set by `AdminGuard` when a signed-in non-admin is bounced off
 *  `/admin/*`: this is where they land, with a sentence saying why instead of a blank
 *  screen or a raw 403 (REB-279's own access rule). */
export function Area() {
  const me = useMe()
  const { denied, github: githubNotice } = useSearch({ strict: false }) as {
    denied?: boolean
    github?: string
  }
  // The same query «Contratti» reads, so it is fetched once: the header needs to know
  // whether something waits for a signature before that tab is opened.
  const contracts = useQuery({
    queryKey: keys.memberContracts,
    queryFn: () => member.contracts(),
    enabled: me.data?.has_card ?? false,
  })
  const [chosen, setChosen] = useState<TabName | null>(null)
  if (!me.data) return null
  const profile = me.data
  const value = profile.has_card ? toApplication(profile) : null
  const requests = profile.requests
  const showPerks = value !== null || profile.role === 'admin'
  const work = profile.work_profile ?? null
  const signable = contracts.data
    ? [
        contracts.data.framework,
        contracts.data.occasional_framework,
        ...contracts.data.previous_frameworks,
        ...contracts.data.letters,
      ]
    : []
  const toSign = signable.some((document) => !!document?.signing_url)
  const hasCv = profile.cv_filename !== null && profile.cv_size !== null
  const answer = (id: string) => (value && FREELANCER_FIELDS.find((field) => field.id === id)?.summary(value)) || '—'
  const rate = profile.daily_rate ? `${formatMoney(profile.daily_rate)} al giorno` : null

  const tabs: TabName[] = [
    ...(value ? (['card', 'profile'] as const) : []),
    ...(requests.length > 0 ? (['requests'] as const) : []),
    ...(value ? (['contracts'] as const) : []),
    'contacts',
  ]
  // The tab that waits for the person, decided once and stored as if they had chosen it.
  // Setting state while rendering is React's own pattern for a value derived once.
  const settled = !value || !contracts.isPending
  if (chosen === null && settled) {
    let waiting: TabName = tabs[0]!
    if (value) {
      if (githubNotice) waiting = 'card'
      else if (toSign) waiting = 'contracts'
      else if (!profile.complete) waiting = 'card'
      else if (work === null || work.state === 'rejected' || work.expired) waiting = 'profile'
    }
    setChosen(waiting)
  }
  const tab: TabName = chosen !== null && tabs.includes(chosen) ? chosen : tabs[0]!

  // What this person is: the singular forms of the admin sidebar's "Talenti", "Aziende" and
  // "Amministratori", with "Freelance" as the member area calls a talent, and «Membro» for
  // somebody who is none of them, so every header names something (REB-601).
  const held = [
    profile.has_card && 'Freelance',
    profile.has_company && 'Azienda',
    profile.role === 'admin' && ROLE_LABELS.admin,
  ].filter((label): label is string => typeof label === 'string')
  const roles = held.length > 0 ? held : [ROLE_LABELS[profile.role] ?? profile.role]

  const facts = [
    value && profile.job_title ? (
      <span key="role" className="font-medium text-foreground">
        {profile.job_title}
      </span>
    ) : null,
    value && rate ? (
      <span key="rate" className="tabular-nums">
        {rate}
      </span>
    ) : null,
    value && profile.remote ? <span key="mode">{REMOTE_LABELS[profile.remote]}</span> : null,
    <span key="email">{profile.email}</span>,
  ].filter((fact) => fact !== null)

  const cardPanel = value && (
    <section aria-labelledby="me-card" className="space-y-4">
      <h2 id="me-card" className="sr-only">
        La tua scheda
      </h2>
      {!profile.complete && (
        // What is missing is what a company would search by, so it is said here and not
        // only in the dashes below. Two ways to get here: a card an admin wrote from a
        // signup (ORB-155), and a person who skipped the CV in the wizard, where it is
        // optional. For the second the CV is the only thing that can be missing.
        <div role="status" className="border-(length:--line-strong) border-accent bg-card p-4 text-sm">
          <p>
            La tua scheda è incompleta. Aggiungi CV, tariffa, posizione e modalità di lavoro
            perché le aziende possano trovarti.
          </p>
          <Button asChild size="sm" className="mt-3">
            <Link to="/me/edit">Completa la scheda</Link>
          </Button>
        </div>
      )}
      <div className="space-y-2">
        <dl className="grid gap-(--line) border bg-border sm:grid-cols-2 xl:grid-cols-3">
          <Cell label="Cosa fai">{answer('job_title')}</Cell>
          <Cell label="Tariffa">{rate ?? '—'}</Cell>
          <Cell label="Come preferisci lavorare">{answer('remote')}</Cell>
          {/* The stored profile is always `https://www.linkedin.com/in/<name>`: the cell shows the part a person reads. */}
          <Cell label="LinkedIn">{answer('linkedin_url').replace(/^https:\/\/www\./, '')}</Cell>
          <Cell label="Altri link">{answer('links')}</Cell>
          <Cell label="CV">
            {hasCv ? (
              // Text, not a flex row: in a third of the width a long file name wraps with its
              // size after it, instead of splitting into columns.
              <a href={member.cvUrl} className="underline-offset-2 [overflow-wrap:anywhere] hover:underline">
                <Download className="mr-1.5 inline size-4 align-text-bottom" aria-hidden="true" />
                {profile.cv_filename}{' '}
                <span className="font-normal text-muted-foreground">({formatBytes(profile.cv_size!)})</span>
              </a>
            ) : (
              <span className="font-normal text-muted-foreground">Nessun CV</span>
            )}
          </Cell>
        </dl>
        <p className="text-sm text-muted-foreground">
          {hasCv
            ? 'La tua scheda anonima nasce da questo CV. Se lo aggiorni, la riscriviamo da qui.'
            : 'La tua scheda anonima nasce dal CV: senza, non possiamo scriverla.'}
        </p>
      </div>
      <MemberGithub github={profile.github} notice={githubNotice ?? null} />
    </section>
  )

  const requestsPanel = requests.length > 0 && (
    <section aria-labelledby="me-requests" className="space-y-3">
      <div className="flex items-center justify-end">
        <h2 id="me-requests" className="sr-only">
          {requests.length === 1 ? 'La tua richiesta' : 'Le tue richieste'}
        </h2>
        <Button asChild variant="outline" size="sm">
          <Link to="/me/new-company">
            <Plus className="mr-2 size-4" />
            Richiedi una nuova figura
          </Link>
        </Button>
      </div>
      <ul className="grid gap-3 xl:grid-cols-2">
        {requests.map((request, index) => (
          <RequestCard key={request.id} request={request} position={index + 1} total={requests.length} />
        ))}
      </ul>
    </section>
  )

  const panels: Record<TabName, ReactNode> = {
    card: cardPanel,
    profile: value && <MemberWorkProfile profile={work} vetted={profile.vetted} />,
    requests: requestsPanel,
    contracts: value && <MemberContracts />,
    contacts: <MemberContacts />,
  }
  const labels: Record<TabName, ReactNode> = {
    card: 'La tua scheda',
    profile: 'Profilo di lavoro',
    requests: (
      <>
        Richieste
        <Badge variant="pill" className="ml-2 tabular-nums">
          {requests.length}
        </Badge>
      </>
    ),
    contracts: (
      <>
        Contratti
        {toSign && (
          <Badge variant="pill" dot="gold" className="ml-2">
            Da firmare
          </Badge>
        )}
      </>
    ),
    contacts: 'Recapiti',
  }

  const perks = showPerks && (
    <section aria-labelledby="me-perks" className="space-y-3 border bg-card p-4 text-sm">
      <h2 id="me-perks" className="font-medium">
        Per chi è dentro
      </h2>
      <Perk title="PigroCRM è tuo, gratis">
        <p className="text-muted-foreground">
          Preventivo, contratto, fattura, ore: fatturare e farti pagare, con i dati fiscali già giusti.
        </p>
        <Button asChild variant="outline" size="sm">
          <a href={PIGROCRM_URL} target="_blank" rel="noopener noreferrer">
            Apri PigroCRM
            <ArrowUpRight className="ml-2 size-4" />
          </a>
        </Button>
      </Perk>
      <Perk title="I primi passi da freelance">
        <p className="text-muted-foreground">
          La parte che nessuno ti spiega prima della prima fattura: come dirti in una frase, come arrivare a un
          numero e difenderlo, cosa scrivere prima di iniziare. Venti minuti.
        </p>
        <Button asChild variant="outline" size="sm">
          <a href={member.guideUrl} onClick={() => capture('guide_downloaded')}>
            <Download className="mr-2 size-4" />
            Scarica la guida
          </a>
        </Button>
        <p className="text-xs text-muted-foreground">
          PDF, {GUIDE.pages} pagine, {GUIDE.kilobytes} KB.
        </p>
      </Perk>
    </section>
  )

  return (
    <>
      {denied && (
        <p role="status" className="mx-6 mt-6 border-(length:--line-strong) border-accent bg-card p-4 text-sm">
          Quella sezione è riservata a chi amministra l’hub: eccoti nella tua area.
        </p>
      )}
      <header className="space-y-3 border-b px-6 py-5">
        <div>
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">La tua area</p>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-2">
            <h1 className="text-2xl font-semibold tracking-tight">
              {profile.first_name} {profile.last_name}
            </h1>
            <div role="group" aria-label="Ruoli" className="flex flex-wrap gap-2">
              {roles.map((label) => (
                <Badge key={label} variant="pill">
                  {label}
                </Badge>
              ))}
            </div>
            {value && work && <ProfilePill profile={work} />}
          </div>
        </div>
        <p className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">{facts}</p>
        {value && (
          <div className="flex flex-wrap items-center gap-2">
            <Button asChild variant="outline" size="sm">
              <Link to="/me/edit">
                <Pencil className="mr-2 size-4" />
                Modifica scheda
              </Link>
            </Button>
            {hasCv && (
              <Button asChild variant="outline" size="sm">
                <a href={member.cvUrl}>
                  <Download className="mr-2 size-4" />
                  CV · {formatBytes(profile.cv_size!)}
                </a>
              </Button>
            )}
          </div>
        )}
      </header>
      <div className="grid items-start gap-6 p-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        {!settled ? (
          <LoadingState className="min-w-0" />
        ) : tabs.length === 1 ? (
          <div className="min-w-0">{panels[tab]}</div>
        ) : (
          <Tabs value={tab} onValueChange={(next) => setChosen(next as TabName)} className="min-w-0">
            <TabBar active={tab}>
              {tabs.map((name) => (
                <TabsTrigger key={name} value={name} className="whitespace-nowrap">
                  {labels[name]}
                </TabsTrigger>
              ))}
            </TabBar>
            {tabs.map((name) => (
              <TabsContent key={name} value={name} forceMount className="pt-4 data-[state=inactive]:hidden">
                {panels[name]}
              </TabsContent>
            ))}
          </Tabs>
        )}
        <aside aria-label="Segnalazioni e vantaggi" className="min-w-0 space-y-4">
          <MemberReferral />
          {perks}
        </aside>
      </div>
    </>
  )
}
