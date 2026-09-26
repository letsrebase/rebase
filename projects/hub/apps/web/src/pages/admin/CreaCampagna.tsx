import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from '@tanstack/react-router'
import { useState } from 'react'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Loader } from '@rebase/ui/loader'
import { admin, ApiError, type Campaign, type CampaignDraft } from '@/lib/api'
import { CAMPAIGN_MAX_LENGTH, defaultSchedule, romeTime } from '@/lib/campaigns'
import { useMe } from '@/lib/me'
import { Header } from './lists'
import { Anteprima, Prova } from './crea-campagna/Anteprima'
import { BarraInvio, type Quando } from './crea-campagna/BarraInvio'
import { Destinatari } from './crea-campagna/Destinatari'
import {
  EMPTY_FORM,
  audienceSource,
  contentReady,
  countAudience,
  defaultNome,
  formFromCampaign,
  keyOf,
  payloadOf,
  withTemplate,
  type CampaignForm,
} from './crea-campagna/form'
import { Messaggio } from './crea-campagna/Messaggio'
import { useAutosave } from './crea-campagna/useAutosave'

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
  const me = useMe()
  const templates = useQuery({ queryKey: ['campaignTemplates'], queryFn: admin.campaignTemplates })

  const [form, setForm] = useState<CampaignForm>(() => (initial ? formFromCampaign(initial) : EMPTY_FORM))
  // Once the admin has written into the mail, picking another state must not overwrite
  // it (spec § 1); the same for the name, on its own. A stored campaign's are the
  // admin's already.
  const [touched, setTouched] = useState({ mail: initial !== null, nome: initial !== null })
  const [esclusi, setEsclusi] = useState<string[]>([])
  const [persona, setPersona] = useState<string | null>(null)
  const [mode, setMode] = useState<Quando>('adesso')
  // Filled when «Programma» is chosen, not when the page opens: a page left open for an
  // hour must not propose a moment already past.
  const [when, setWhen] = useState({ giorno: '', ora: '' })

  const payload = payloadOf(form)
  const key = keyOf(payload)
  const save = useAutosave(key, initial)
  const { campaign } = save
  const dirty = key !== save.savedKey

  const audience = useQuery({
    queryKey: ['campaignAudience', campaign?.id, campaign ? audienceSource(campaign) : null],
    queryFn: () => admin.campaignAudience(campaign!.id),
    enabled: campaign !== null,
    placeholderData: keepPreviousData,
  })
  // The list on screen is the saved source's: while a changed filter waits for its save,
  // or the list for its reload, it says so rather than pass for the new one.
  const savedPayload = save.savedKey ? (JSON.parse(save.savedKey) as CampaignDraft) : null
  const stale =
    audience.isPlaceholderData ||
    (payload !== null && savedPayload !== null && audienceSource(payload) !== audienceSource(savedPayload))
  const count = audience.data ? countAudience(audience.data, esclusi) : null
  // Until the first save lands there is no list to load, so a failed create is the
  // list's failure too.
  const listFailure = failureMessage(audience.error) ?? (campaign === null ? failureMessage(save.error) : null)
  const riceventi = audience.data?.righe.filter((row) => row.escluso === null && !esclusi.includes(row.email)) ?? []
  const personaRow = riceventi.find((row) => row.email === persona) ?? riceventi[0] ?? null

  // Another source is another list: an untick made on the old one must not leave out
  // someone who is on the new one too.
  function changeSource(patch: Partial<CampaignForm>) {
    setForm((current) => ({ ...current, ...patch }))
    setEsclusi([])
  }
  function changeMail(patch: Partial<CampaignForm>) {
    setForm((current) => ({ ...current, ...patch }))
    setTouched((current) => ({ ...current, mail: true }))
  }
  function changeNome(nome: string) {
    setForm((current) => ({ ...current, nome }))
    setTouched((current) => ({ ...current, nome: true }))
  }
  function pickTemplate(value: string) {
    const template = templates.data?.find((item) => item.stato_percorso === value)
    setForm((current) => withTemplate(current, template, value, touched))
    setEsclusi([])
  }
  function toggle(email: string, included: boolean) {
    setEsclusi((current) => (included ? current.filter((item) => item !== email) : [...current, email]))
  }
  function chooseMode(next: Quando) {
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
      const esclusiOra = count.esclusi
      const moment = mode === 'programma' ? { giorno: when.giorno, ora: when.ora } : {}
      return save.run(key, (saved) => admin.scheduleCampaign(saved.id, { ...moment, esclusi: esclusiOra }))
    },
    onSuccess: (sent) => void navigate({ to: '/admin/campaigns/$id', params: { id: sent.id } }),
  })

  function blockedReason(): string | null {
    if (payload === null) return 'Scegli prima a chi scrivere.'
    if (!contentReady(form)) return 'Scrivi oggetto, testo e bottone della mail.'
    if (dirty && save.error) return `Le modifiche non sono salvate: ${failureMessage(save.error)}`
    if (dirty || save.saving) return 'Salvo le modifiche…'
    if (audience.error) return `L’elenco non si carica: ${failureMessage(audience.error)}`
    if (count === null || stale) return 'Carico l’elenco…'
    if (count.riceveranno === 0) return 'Nessuno riceverebbe la mail.'
    if (!campaign?.prova_inviata_at) return 'Manda prima una prova: il bottone è sotto l’anteprima.'
    if (!campaign.pronta) return 'Hai cambiato la campagna dopo la prova: mandane un’altra.'
    if (mode === 'programma' && (!when.giorno || !when.ora)) return 'Scegli giorno e ora.'
    return null
  }

  return (
    <div className="flex min-h-full flex-col">
      <header className="flex flex-wrap items-end justify-between gap-3 border-b px-6 py-5">
        <div className="min-w-0 flex-1 space-y-1">
          <h1 className="text-sm text-muted-foreground">{initial ? 'Modifica campagna' : 'Nuova campagna'}</h1>
          <Label htmlFor="campagna-nome" className="sr-only">
            Nome della campagna
          </Label>
          <Input
            id="campagna-nome"
            maxLength={CAMPAIGN_MAX_LENGTH.nome}
            placeholder={defaultNome(form.fonte)}
            value={form.nome}
            onChange={(event) => changeNome(event.target.value)}
            className="-mx-2.5 h-auto max-w-xl border-transparent py-0.5 text-2xl font-semibold tracking-tight hover:border-border md:text-2xl"
          />
        </div>
        <SaveStatus saving={save.saving} error={save.error} savedAt={save.savedAt} />
      </header>
      <div className="grid flex-1 gap-x-10 gap-y-8 p-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]">
        <div className="min-w-0 space-y-10">
          <Destinatari
            form={form}
            templates={templates.data ?? []}
            onChange={changeSource}
            onTemplate={pickTemplate}
            audience={audience.data}
            count={count}
            stale={stale}
            audienceError={listFailure}
            esclusi={esclusi}
            onToggle={toggle}
          />
          <Messaggio form={form} onChange={changeMail} />
        </div>
        <aside className="min-w-0 space-y-4 lg:sticky lg:top-6 lg:self-start">
          <Anteprima
            oggetto={form.oggetto}
            testo={form.testo}
            bottoneTesto={form.bottoneTesto}
            righe={riceventi}
            persona={personaRow}
            onPersona={setPersona}
          />
          <Prova
            campaign={campaign}
            dirty={dirty}
            email={me.data?.email}
            ready={payload !== null && contentReady(form)}
            pending={test.isPending}
            failure={failureMessage(test.error)}
            onTest={() => test.mutate()}
          />
        </aside>
      </div>
      <BarraInvio
        mode={mode}
        onMode={chooseMode}
        when={when}
        onWhen={setWhen}
        riceveranno={count?.riceveranno ?? null}
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
export function AdminCreaCampagna() {
  const { id } = useParams({ strict: false }) as { id?: string }
  const editing = useQuery({
    queryKey: ['campaign', id],
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
  const stored = editing.data.campagna
  if (stored.stato !== 'bozza') {
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
