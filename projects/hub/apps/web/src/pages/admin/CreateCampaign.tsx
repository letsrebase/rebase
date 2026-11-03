import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from '@tanstack/react-router'
import { useState } from 'react'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Loader } from '@rebase/ui/loader'
import { admin, ApiError, type Campaign, type CampaignDraft, type CampaignMeta } from '@/lib/api'
import { CAMPAIGN_MAX_LENGTH, defaultSchedule, romeTime } from '@/lib/campaigns'
import { useMe } from '@/lib/me'
import { keys } from '@/lib/query-keys'
import { Header } from './list-kit'
import { Preview, Test } from './create-campaign/Preview'
import { BarSend, type When } from './create-campaign/SendBar'
import { Recipients } from './create-campaign/Recipients'
import {
  EMPTY_FORM,
  audienceSource,
  contentReady,
  countAudience,
  defaultName,
  formFromCampaign,
  keyOf,
  linkProblem,
  payloadOf,
  withMeta,
  withTemplate,
  type CampaignForm,
} from './create-campaign/form'
import { Message } from './create-campaign/Message'
import { useAutosave } from './create-campaign/useAutosave'

function failureMessage(error: unknown): string | null {
  if (!error) return null
  if (error instanceof ApiError) return error.message
  return 'Qualcosa è andato storto.'
}

function SaveStatus({ saving, error, savedAt }: { saving: boolean; error: unknown; savedAt: string | null }) {
  return (
    <div className="text-sm" aria-live="polite">
      {saving ? (
        <p className="text-muted-foreground">Salvo…</p>
      ) : error ? (
        <p role="alert" className="text-destructive">
          Non salvata: {failureMessage(error)}
        </p>
      ) : savedAt ? (
        <p className="text-muted-foreground">Bozza salvata alle {romeTime(savedAt)}</p>
      ) : (
        <p className="text-muted-foreground">La bozza si salva da sola.</p>
      )}
    </div>
  )
}

/** The page itself (REB-526): the name as its title, Destinatari and Messaggio on the
 *  left, the mail's preview and the test on the right, the send bar at the foot. The
 *  draft saves itself; the list follows the saved source; «Invia» names the count and
 *  the moment, and says why when it is off. `initial` is the stored campaign on the
 *  edit route, `null` on «Nuova campagna». */
function Editor({ initial }: { initial: Campaign | null }) {
  const navigate = useNavigate()
  const client = useQueryClient()
  const me = useMe()
  const templates = useQuery({ queryKey: keys.campaignTemplates, queryFn: admin.campaignTemplates })

  const [form, setForm] = useState<CampaignForm>(() => (initial ? formFromCampaign(initial) : EMPTY_FORM))
  // Once the admin has written into the mail, picking another state must not overwrite
  // it (spec § 1); the same for the name and for «Dove porta», each on its own: picking
  // a destination is not writing the mail, so a state picked after it still fills the
  // subject, the text and the button (REB-530). A stored campaign's are the admin's
  // already.
  const [touched, setTouched] = useState({ mail: initial !== null, nome: initial !== null, meta: initial !== null })
  const [excluded, setExcluded] = useState<string[]>([])
  const [person, setPerson] = useState<string | null>(null)
  const [mode, setMode] = useState<When>('adesso')
  // Filled when «Programma» is chosen, not when the page opens: a page left open for an
  // hour must not propose a moment already past.
  const [when, setWhen] = useState({ day: '', ora: '' })

  // The parent's name, for the sentence and the link in Destinatari (Task 6): a `list`
  // never picks its own audience, so it names whose it inherited instead.
  const parent = useQuery({
    queryKey: keys.campaign(form.segueId),
    queryFn: () => admin.campaign(form.segueId!),
    enabled: form.source === 'list' && form.segueId !== null,
  })
  const segue = parent.data ? { id: parent.data.campaign.id, nome: parent.data.campaign.nome } : null
  const segueError = parent.isError ? failureMessage(parent.error) : null

  const payload = payloadOf(form)
  const key = keyOf(payload)
  // «Un link» without a valid address is not saved at all (REB-530): the server would
  // refuse it, and a half-typed address is not a failure worth a request. The field
  // says why, and so do the header and the send bar.
  const linkError = linkProblem(form)
  const save = useAutosave(linkError ? null : key, initial)
  const { campaign } = save
  const dirty = key !== save.savedKey
  const saveError = linkError ? new ApiError(422, linkError, ['button_url']) : save.error
  // What the server refused about the address of the draft on screen, said under the
  // field too: a rule the editor lacks is still explained where the admin types, and
  // the other fields go on saving as soon as the address changes (REB-530).
  const linkRefused =
    key !== null && key === save.failedKey && save.error instanceof ApiError && save.error.fields.includes('button_url')
      ? save.error.message
      : null

  const audience = useQuery({
    queryKey: keys.campaignAudience(campaign?.id, campaign ? audienceSource(campaign) : null),
    queryFn: () => admin.campaignAudience(campaign!.id),
    enabled: campaign !== null,
    placeholderData: keepPreviousData,
  })
  // The list on screen is the saved source's: while a changed filter waits for its save,
  // or the list for its reload, it says so rather than pass for the new one.
  const savedPayload = save.savedKey ? (JSON.parse(save.savedKey) as CampaignDraft) : null
  const stale =
    audience.isPlaceholderData ||
    // `payload` may be a `ListPatch`, which carries none of `audienceSource`'s fields
    // on purpose (a `list`'s key is always `[null, null, null]`): the cast changes
    // nothing at runtime, only what the type checker already knows from `form.source`.
    (payload !== null && savedPayload !== null && audienceSource(payload as CampaignDraft) !== audienceSource(savedPayload))
  const count = audience.data ? countAudience(audience.data, excluded) : null
  // Until the first save lands there is no list to load, so a failed create is the
  // list's failure too.
  const listFailure = failureMessage(audience.error) ?? (campaign === null ? failureMessage(save.error) : null)
  const receivers = audience.data?.lines.filter((row) => row.excluded === null && !excluded.includes(row.email)) ?? []
  const personRow = receivers.find((row) => row.email === person) ?? receivers[0] ?? null

  // Another source is another list: an untick made on the old one must not leave out
  // someone who is on the new one too.
  function changeSource(patch: Partial<CampaignForm>) {
    setForm((current) => ({ ...current, ...patch }))
    setExcluded([])
  }
  function changeMail(patch: Partial<CampaignForm>) {
    setForm((current) => ({ ...current, ...patch }))
    setTouched((current) => ({ ...current, mail: true }))
  }
  function changeMeta(meta: CampaignMeta) {
    setForm((current) =>
      withMeta(current, meta, templates.data?.find((item) => item.journey_status === current.journeyStatus)?.action),
    )
    setTouched((current) => ({ ...current, meta: true }))
  }
  function changeLink(buttonUrl: string) {
    setForm((current) => ({ ...current, buttonUrl }))
    setTouched((current) => ({ ...current, meta: true }))
  }
  function changeName(nome: string) {
    setForm((current) => ({ ...current, nome }))
    setTouched((current) => ({ ...current, nome: true }))
  }
  function pickTemplate(value: string) {
    const template = templates.data?.find((item) => item.journey_status === value)
    setForm((current) => withTemplate(current, template, value, touched))
    setExcluded([])
  }
  function toggle(email: string, included: boolean) {
    setExcluded((current) => (included ? current.filter((item) => item !== email) : [...current, email]))
  }
  function chooseMode(next: When) {
    if (next === 'programma' && mode !== 'programma') setWhen(defaultSchedule())
    setMode(next)
  }

  const test = useMutation({
    mutationFn: () => {
      if (key === null) throw new Error('nothing to test')
      return save.run(key, (saved) => admin.testCampaign(saved.id))
    },
  })
  const schedule = useMutation({
    mutationFn: () => {
      if (key === null || count === null) throw new Error('nothing to send')
      const excludedHour = count.excluded
      const moment = mode === 'programma' ? { day: when.day, ora: when.ora } : {}
      return save.run(key, (saved) => admin.scheduleCampaign(saved.id, { ...moment, excluded: excludedHour }))
    },
    onSuccess: (sent) => {
      void client.invalidateQueries({ queryKey: keys.campaigns })
      void navigate({ to: '/admin/campaigns/$id', params: { id: sent.id } })
    },
  })

  function blockedReason(): string | null {
    if (payload === null) return 'Scegli prima a chi scrivere.'
    if (!contentReady(form)) return 'Scrivi oggetto, testo e bottone della mail.'
    if (linkError) return linkError
    if (dirty && save.error) return `Le modifiche non sono salvate: ${failureMessage(save.error)}`
    if (dirty || save.saving) return 'Salvo le modifiche…'
    if (audience.error) return `L’elenco non si carica: ${failureMessage(audience.error)}`
    if (count === null || stale) return 'Carico l’elenco…'
    if (count.will_receive === 0) return 'Nessuno riceverebbe la mail.'
    if (!campaign?.test_sent_at) return 'Manda prima una prova: il bottone è sotto l’anteprima.'
    if (!campaign.ready) return 'Hai cambiato la campagna dopo la prova: mandane un’altra.'
    if (mode === 'programma' && (!when.day || !when.ora)) return 'Scegli giorno e ora.'
    return null
  }

  return (
    <div className="flex min-h-full flex-col">
      <header className="flex flex-wrap items-end justify-between gap-3 border-b px-6 py-5">
        <div className="min-w-0 flex-1 space-y-1">
          <h1 className="text-sm text-muted-foreground">
            {form.source === 'list' ? 'Riscrivi a chi non ha fatto niente' : initial ? 'Modifica campagna' : 'Nuova campagna'}
          </h1>
          <Label htmlFor="campagna-nome" className="sr-only">
            Nome della campagna
          </Label>
          <Input
            id="campagna-nome"
            maxLength={CAMPAIGN_MAX_LENGTH.nome}
            placeholder={defaultName(form.source)}
            value={form.nome}
            onChange={(event) => changeName(event.target.value)}
            className="-mx-2.5 h-auto max-w-xl border-transparent py-0.5 text-2xl font-semibold tracking-tight hover:border-border md:text-2xl"
          />
        </div>
        <SaveStatus saving={save.saving} error={saveError} savedAt={save.savedAt} />
      </header>
      <div className="grid flex-1 gap-x-10 gap-y-8 p-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]">
        <div className="min-w-0 space-y-10">
          <Recipients
            form={form}
            templates={templates.data ?? []}
            onChange={changeSource}
            onTemplate={pickTemplate}
            audience={audience.data}
            count={count}
            stale={stale}
            audienceError={listFailure}
            excluded={excluded}
            onToggle={toggle}
            segue={segue}
            segueError={segueError}
          />
          <Message form={form} onChange={changeMail} onMeta={changeMeta} onLink={changeLink} linkRefused={linkRefused} />
        </div>
        <aside className="min-w-0 space-y-4 lg:sticky lg:top-6 lg:self-start">
          <Preview
            subject={form.subject}
            text={form.text}
            buttonText={form.buttonText}
            link={form.buttonMeta === 'link' && !linkError ? form.buttonUrl.trim() : null}
            lines={receivers}
            person={personRow}
            onPerson={setPerson}
          />
          <Test
            campaign={campaign}
            dirty={dirty}
            email={me.data?.email}
            ready={payload !== null && contentReady(form) && !linkError}
            pending={test.isPending}
            failure={failureMessage(test.error)}
            onTest={() => test.mutate()}
          />
        </aside>
      </div>
      <BarSend
        mode={mode}
        onMode={chooseMode}
        when={when}
        onWhen={setWhen}
        will_receive={count?.will_receive ?? null}
        blocked={blockedReason()}
        pending={schedule.isPending}
        failure={failureMessage(schedule.error)}
        onSend={() => schedule.mutate()}
      />
    </div>
  )
}

/** «Nuova campagna» and «Modifica campagna» (P-REB-41, REB-526): one page instead of
 *  REB-472's four steps. The edit route waits for the stored campaign, read afresh, and
 *  hands it to the editor as its starting point; a campaign that has left «bozza» is
 *  not editable, and the page says so instead of offering fields the API would refuse. */
export function AdminCreateCampaign() {
  const { id } = useParams({ strict: false }) as { id?: string }
  const editing = useQuery({
    queryKey: keys.campaign(id),
    queryFn: () => admin.campaign(id!),
    enabled: id !== undefined,
  })
  if (id === undefined) return <Editor initial={null} />
  if (editing.isError) {
    return (
      <>
        <Header title="Modifica campagna" />
        <p role="alert" className="p-6 text-sm text-destructive">
          {failureMessage(editing.error)}
        </p>
      </>
    )
  }
  if (!editing.data || !editing.isFetchedAfterMount) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <Loader className="size-5" />
        Carico la campagna…
      </div>
    )
  }
  const stored = editing.data.campaign
  if (stored.status !== 'draft') {
    return (
      <>
        <Header title="Modifica campagna" />
        <p className="p-6 text-sm">
          «{stored.nome}» non è più una bozza, quindi non si modifica.{' '}
          <Link to="/admin/campaigns/$id" params={{ id: stored.id }} className="underline">
            Torna alla campagna
          </Link>
        </p>
      </>
    )
  }
  return <Editor key={stored.id} initial={stored} />
}
