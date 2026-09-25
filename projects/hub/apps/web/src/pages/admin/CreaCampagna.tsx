import { useMutation, useQuery } from '@tanstack/react-query'
import { useNavigate, useParams } from '@tanstack/react-router'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Textarea } from '@rebase/ui/textarea'
import {
  admin,
  ApiError,
  type AudiencePreview,
  type Campaign,
  type CampaignAzione,
  type CampaignDraft,
  type CampaignMeta,
} from '@/lib/api'
import { AZIONE_LABELS, META_LABELS, personalise, romeToday } from '@/lib/campaigns'
import { useMe } from '@/lib/me'
import { Header } from './lists'

const STEPS = ['Chi', 'Cosa', 'Prova', 'Quando'] as const

type Fonte = 'stato' | 'filtri'
type Lista = 'talenti' | 'aziende'

// A `Select` cannot take an item with value `""` -- Radix reserves it for "nothing
// picked yet", which is exactly what an unset filter or an unset template is here.
const NONE = ''
const ANY = 'tutti'

function boolToSelect(value: boolean | undefined): string {
  return value === undefined ? ANY : value ? 'si' : 'no'
}

function selectToBool(value: string): boolean | undefined {
  return value === 'si' ? true : value === 'no' ? false : undefined
}

interface TalentiFiltriForm {
  stato: string
  q: string
  has_cv: string
  con_accessi: string
}

interface AziendeFiltriForm {
  stato: string
  q: string
}

const TALENTI_FILTRI_EMPTY: TalentiFiltriForm = { stato: '', q: '', has_cv: ANY, con_accessi: ANY }
const AZIENDE_FILTRI_EMPTY: AziendeFiltriForm = { stato: '', q: '' }

/** What the edit route seeds Chi's own filter fields with, out of `Campaign.filtri`
 *  (a bag the server never types further): a plain function so the effect that calls
 *  it stays a flat list of `setState`s, none of them behind a nested condition. */
function seedFiltri(c: Campaign): { lista: Lista; talenti: TalentiFiltriForm; aziende: AziendeFiltriForm } {
  const empty = { lista: 'talenti' as Lista, talenti: TALENTI_FILTRI_EMPTY, aziende: AZIENDE_FILTRI_EMPTY }
  if (c.fonte !== 'filtri' || !c.filtri) return empty
  const f = c.filtri as Record<string, unknown>
  const stato = typeof f.stato === 'string' ? f.stato : ''
  const q = typeof f.q === 'string' ? f.q : ''
  if ('has_cv' in f || 'con_accessi' in f) {
    return {
      lista: 'talenti',
      talenti: {
        stato,
        q,
        has_cv: boolToSelect(f.has_cv as boolean | undefined),
        con_accessi: boolToSelect(f.con_accessi as boolean | undefined),
      },
      aziende: AZIENDE_FILTRI_EMPTY,
    }
  }
  return { lista: 'aziende', talenti: TALENTI_FILTRI_EMPTY, aziende: { stato, q } }
}

function failureMessage(error: unknown): string | null {
  if (!error) return null
  if (error instanceof ApiError) return error.message
  return 'Qualcosa è andato storto.'
}

function Failure({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <p role="alert" className="text-sm text-destructive">
      {message}
    </p>
  )
}

function Footer({
  onBack,
  next,
  pending,
  ready = true,
  failure,
}: {
  onBack?: () => void
  next: string
  pending: boolean
  ready?: boolean
  failure: string | null
}) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {onBack && (
          <Button type="button" variant="outline" onClick={onBack}>
            Indietro
          </Button>
        )}
        <Button type="submit" disabled={pending || !ready}>
          {pending ? 'Un momento…' : next}
        </Button>
      </div>
      <Failure message={failure} />
    </div>
  )
}

function AudienceTable({
  audience,
  esclusi,
  onToggle,
}: {
  audience: AudiencePreview
  esclusi: string[]
  onToggle: (email: string, included: boolean) => void
}) {
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        {audience.incluse} incluse, {audience.escluse} escluse
      </p>
      <div className="overflow-x-auto border border-border">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b bg-muted/50 text-left">
              <th className="px-3 py-1.5 font-medium">Nome</th>
              <th className="px-3 py-1.5 font-medium">Indirizzo</th>
              <th className="px-3 py-1.5 font-medium">Includi</th>
            </tr>
          </thead>
          <tbody>
            {audience.righe.map((row) => {
              const forced = row.escluso !== null
              const checked = forced || !esclusi.includes(row.email)
              return (
                <tr key={row.email} className="border-b last:border-0">
                  <td className="px-3 py-1.5">{row.nome ?? '—'}</td>
                  <td className="px-3 py-1.5">{row.email}</td>
                  <td className="px-3 py-1.5">
                    <div className="flex items-center gap-2">
                      <Checkbox
                        aria-label={row.email}
                        checked={checked}
                        disabled={forced}
                        onCheckedChange={(value) => onToggle(row.email, value === true)}
                      />
                      {row.escluso && <span className="text-xs text-muted-foreground">{row.escluso}</span>}
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/** «Nuova campagna» (P-REB-41, REB-472): four steps from a state or a filter to a
 *  scheduled send, the same shape `CreaMatch.tsx` uses -- step state, an ordered list
 *  of step names and a footer with «Indietro»/«Avanti». Chi's own «Avanti» does double
 *  duty: the first press writes the draft and loads the audience preview without
 *  leaving the step, so the admin sees who is left out and why before moving on; the
 *  next press advances to Cosa. The edit route (`/admin/campaigns/$id/edit`) loads the
 *  existing campaign and starts here too -- the API itself refuses a save once the
 *  campaign has left «bozza», and that refusal surfaces the same way any other one
 *  does, in the step's own alert. */
export function AdminCreaCampagna() {
  const { id: idParam } = useParams({ strict: false }) as { id?: string }
  const navigate = useNavigate()
  const me = useMe()

  const [step, setStep] = useState(0)
  const [campaign, setCampaign] = useState<Campaign | null>(null)

  const [fonte, setFonte] = useState<Fonte>('stato')
  const [statoPercorso, setStatoPercorso] = useState<string | null>(null)
  const [lista, setLista] = useState<Lista>('talenti')
  const [talentiFiltri, setTalentiFiltri] = useState<TalentiFiltriForm>(TALENTI_FILTRI_EMPTY)
  const [aziendeFiltri, setAziendeFiltri] = useState<AziendeFiltriForm>(AZIENDE_FILTRI_EMPTY)

  const [nome, setNome] = useState('')
  const [oggetto, setOggetto] = useState('')
  const [testo, setTesto] = useState('')
  const [bottoneTesto, setBottoneTesto] = useState('')
  const [bottoneMeta, setBottoneMeta] = useState<CampaignMeta>('area')
  const [azione, setAzione] = useState<CampaignAzione>('entrato')
  // Once the admin has written into Cosa's own fields, picking another state must not
  // overwrite what they wrote (spec § 1).
  const [contentTouched, setContentTouched] = useState(false)

  const [audience, setAudience] = useState<AudiencePreview | null>(null)
  const [esclusi, setEsclusi] = useState<string[]>([])

  const [mode, setMode] = useState<'adesso' | 'programma'>('adesso')
  const [when, setWhen] = useState(() => romeToday())

  const templates = useQuery({ queryKey: ['campaignTemplates'], queryFn: admin.campaignTemplates })
  const editing = useQuery({
    queryKey: ['campaign', idParam],
    queryFn: () => admin.campaign(idParam!),
    enabled: idParam !== undefined,
  })

  const seeded = useRef(false)
  useEffect(() => {
    if (!editing.data || seeded.current) return
    seeded.current = true
    const c = editing.data.campagna
    const filtri = seedFiltri(c)
    setCampaign(c)
    setFonte(c.fonte === 'filtri' ? 'filtri' : 'stato')
    setStatoPercorso(c.stato_percorso)
    setLista(filtri.lista)
    setTalentiFiltri(filtri.talenti)
    setAziendeFiltri(filtri.aziende)
    setNome(c.nome)
    setOggetto(c.oggetto)
    setTesto(c.testo)
    setBottoneTesto(c.bottone_testo)
    setBottoneMeta(c.bottone_meta)
    setAzione(c.azione)
    // Already saved content: a template pick from here on must not clobber it.
    setContentTouched(true)
  }, [editing.data])

  function selectTemplate(value: string) {
    setStatoPercorso(value)
    setAudience(null)
    setEsclusi([])
    const template = templates.data?.find((item) => item.stato_percorso === value)
    if (!template || contentTouched) return
    setNome(template.etichetta)
    setOggetto(template.oggetto)
    setTesto(template.testo)
    setBottoneTesto(template.bottone_testo)
    setBottoneMeta(template.bottone_meta)
    setAzione(template.azione)
  }

  function chooseFonte(next: Fonte) {
    if (next === fonte) return
    setFonte(next)
    setAudience(null)
    setEsclusi([])
  }

  function filtriPayload(): Record<string, unknown> | null {
    if (fonte !== 'filtri') return null
    if (lista === 'talenti') {
      return {
        lista,
        stato: talentiFiltri.stato || undefined,
        q: talentiFiltri.q || undefined,
        has_cv: selectToBool(talentiFiltri.has_cv),
        con_accessi: selectToBool(talentiFiltri.con_accessi),
      }
    }
    return { lista, stato: aziendeFiltri.stato || undefined, q: aziendeFiltri.q || undefined }
  }

  function buildPayload(): CampaignDraft {
    return {
      nome: nome.trim(),
      fonte,
      stato_percorso: fonte === 'stato' ? statoPercorso : null,
      filtri: filtriPayload(),
      oggetto,
      testo,
      bottone_testo: bottoneTesto,
      bottone_meta: bottoneMeta,
      azione,
    }
  }

  const advanceChi = useMutation({
    mutationFn: async () => {
      const payload = buildPayload()
      const saved = campaign ? await admin.updateCampaign(campaign.id, payload) : await admin.createCampaign(payload)
      const preview = await admin.campaignAudience(saved.id)
      return { saved, preview }
    },
    onSuccess: ({ saved, preview }) => {
      setCampaign(saved)
      setAudience(preview)
      setEsclusi([])
    },
  })

  function chiNext() {
    if (audience) {
      setStep(1)
      return
    }
    advanceChi.mutate()
  }

  const saveCosa = useMutation({
    mutationFn: () => admin.updateCampaign(campaign!.id, buildPayload()),
    onSuccess: (saved) => {
      setCampaign(saved)
      setStep(2)
    },
  })

  const testCampaign = useMutation({
    mutationFn: () => admin.testCampaign(campaign!.id),
    onSuccess: (saved) => setCampaign(saved),
  })

  const schedule = useMutation({
    mutationFn: () =>
      admin.scheduleCampaign(campaign!.id, mode === 'programma' ? { giorno: when.giorno, ora: when.ora, esclusi } : { esclusi }),
    onSuccess: (saved) => void navigate({ to: '/admin/campaigns/$id', params: { id: saved.id } }),
  })

  function toggleEsclusione(email: string, included: boolean) {
    setEsclusi((current) => (included ? current.filter((item) => item !== email) : [...current, email]))
  }

  const chiReady = fonte === 'stato' ? statoPercorso !== null : true
  const cosaReady = nome.trim() !== '' && oggetto.trim() !== '' && testo.trim() !== '' && bottoneTesto.trim() !== ''
  const firstIncluded = audience?.righe.find((row) => row.escluso === null) ?? null

  return (
    <>
      <Header title={idParam ? 'Modifica campagna' : 'Nuova campagna'} />
      <ol aria-label="Passi" className="flex flex-wrap gap-x-4 gap-y-1 border-b px-6 py-3 text-sm">
        {STEPS.map((label, index) => (
          <li
            key={label}
            aria-current={index === step ? 'step' : undefined}
            className={index === step ? 'font-medium' : 'text-muted-foreground'}
          >
            {index + 1}. {label}
          </li>
        ))}
      </ol>
      <div className="max-w-3xl space-y-4 p-6">
        {step === 0 && (
          <form
            className="space-y-4"
            onSubmit={(event: FormEvent) => {
              event.preventDefault()
              chiNext()
            }}
          >
            <div className="flex flex-wrap gap-2" role="group" aria-label="Da chi parte la campagna">
              <Button
                type="button"
                variant={fonte === 'stato' ? 'default' : 'outline'}
                aria-pressed={fonte === 'stato'}
                onClick={() => chooseFonte('stato')}
              >
                Uno stato del percorso
              </Button>
              <Button
                type="button"
                variant={fonte === 'filtri' ? 'default' : 'outline'}
                aria-pressed={fonte === 'filtri'}
                onClick={() => chooseFonte('filtri')}
              >
                Filtri
              </Button>
            </div>
            {fonte === 'stato' && (
              <div className="max-w-sm space-y-1.5">
                <Label htmlFor="campagna-stato-percorso">Stato del percorso</Label>
                <Select value={statoPercorso ?? NONE} onValueChange={selectTemplate}>
                  <SelectTrigger id="campagna-stato-percorso" aria-label="Stato del percorso" className="w-full">
                    <SelectValue placeholder="Scegli uno stato" />
                  </SelectTrigger>
                  <SelectContent>
                    {(templates.data ?? []).map((template) => (
                      <SelectItem key={template.stato_percorso} value={template.stato_percorso}>
                        {template.etichetta}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
            {fonte === 'filtri' && (
              <div className="space-y-4">
                <div className="max-w-sm space-y-1.5">
                  <Label htmlFor="campagna-lista">Lista</Label>
                  <Select
                    value={lista}
                    onValueChange={(value) => {
                      setLista(value as Lista)
                      setAudience(null)
                      setEsclusi([])
                    }}
                  >
                    <SelectTrigger id="campagna-lista" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="talenti">Talenti</SelectItem>
                      <SelectItem value="aziende">Aziende</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {lista === 'talenti' ? (
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div className="space-y-1.5">
                      <Label htmlFor="campagna-talenti-stato">Stato</Label>
                      <Input
                        id="campagna-talenti-stato"
                        value={talentiFiltri.stato}
                        onChange={(event) => setTalentiFiltri({ ...talentiFiltri, stato: event.target.value })}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="campagna-talenti-q">Cerca</Label>
                      <Input
                        id="campagna-talenti-q"
                        value={talentiFiltri.q}
                        onChange={(event) => setTalentiFiltri({ ...talentiFiltri, q: event.target.value })}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="campagna-talenti-cv">Ha un CV</Label>
                      <Select
                        value={talentiFiltri.has_cv}
                        onValueChange={(value) => setTalentiFiltri({ ...talentiFiltri, has_cv: value })}
                      >
                        <SelectTrigger id="campagna-talenti-cv" className="w-full">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value={ANY}>Tutti</SelectItem>
                          <SelectItem value="si">Sì</SelectItem>
                          <SelectItem value="no">No</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="campagna-talenti-accessi">Ha fatto accesso</Label>
                      <Select
                        value={talentiFiltri.con_accessi}
                        onValueChange={(value) => setTalentiFiltri({ ...talentiFiltri, con_accessi: value })}
                      >
                        <SelectTrigger id="campagna-talenti-accessi" className="w-full">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value={ANY}>Tutti</SelectItem>
                          <SelectItem value="si">Sì</SelectItem>
                          <SelectItem value="no">No</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                ) : (
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div className="space-y-1.5">
                      <Label htmlFor="campagna-aziende-stato">Stato</Label>
                      <Input
                        id="campagna-aziende-stato"
                        value={aziendeFiltri.stato}
                        onChange={(event) => setAziendeFiltri({ ...aziendeFiltri, stato: event.target.value })}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="campagna-aziende-q">Cerca</Label>
                      <Input
                        id="campagna-aziende-q"
                        value={aziendeFiltri.q}
                        onChange={(event) => setAziendeFiltri({ ...aziendeFiltri, q: event.target.value })}
                      />
                    </div>
                  </div>
                )}
              </div>
            )}
            {audience && <AudienceTable audience={audience} esclusi={esclusi} onToggle={toggleEsclusione} />}
            <Footer next="Avanti" pending={advanceChi.isPending} ready={chiReady} failure={failureMessage(advanceChi.error)} />
          </form>
        )}
        {step === 1 && campaign && (
          <form
            className="max-w-xl space-y-4"
            onSubmit={(event: FormEvent) => {
              event.preventDefault()
              saveCosa.mutate()
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="campagna-nome">Nome</Label>
              <Input
                id="campagna-nome"
                required
                maxLength={200}
                value={nome}
                onChange={(event) => {
                  setNome(event.target.value)
                  setContentTouched(true)
                }}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="campagna-oggetto">Oggetto</Label>
              <Input
                id="campagna-oggetto"
                required
                maxLength={300}
                value={oggetto}
                onChange={(event) => {
                  setOggetto(event.target.value)
                  setContentTouched(true)
                }}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="campagna-testo">Testo</Label>
              <Textarea
                id="campagna-testo"
                required
                rows={8}
                value={testo}
                onChange={(event) => {
                  setTesto(event.target.value)
                  setContentTouched(true)
                }}
              />
              <p className="text-xs text-muted-foreground">{'{nome}'} diventa il nome della persona</p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="campagna-bottone-testo">Testo del bottone</Label>
              <Input
                id="campagna-bottone-testo"
                required
                maxLength={80}
                value={bottoneTesto}
                onChange={(event) => {
                  setBottoneTesto(event.target.value)
                  setContentTouched(true)
                }}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="campagna-bottone-meta">Dove porta</Label>
              <Select
                value={bottoneMeta}
                onValueChange={(value) => {
                  setBottoneMeta(value as CampaignMeta)
                  setContentTouched(true)
                }}
              >
                <SelectTrigger id="campagna-bottone-meta" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(META_LABELS).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="campagna-azione">Azione</Label>
              <Select
                value={azione}
                onValueChange={(value) => {
                  setAzione(value as CampaignAzione)
                  setContentTouched(true)
                }}
                disabled={fonte === 'stato'}
              >
                <SelectTrigger id="campagna-azione" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(AZIONE_LABELS)
                    .filter(([value]) => value !== 'pigro_cliente')
                    .map(([value, label]) => (
                      <SelectItem key={value} value={value}>
                        {label}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </div>
            <Footer
              onBack={() => setStep(0)}
              next="Avanti"
              pending={saveCosa.isPending}
              ready={cosaReady}
              failure={failureMessage(saveCosa.error)}
            />
          </form>
        )}
        {step === 2 && campaign && (
          <div className="space-y-4">
            <div className="max-w-md space-y-3 border p-4">
              <p className="text-sm">
                <span className="font-medium">Oggetto:</span> {oggetto}
              </p>
              <p className="whitespace-pre-wrap text-sm">{personalise(testo, firstIncluded?.nome ?? null)}</p>
              <Button type="button" disabled>
                {bottoneTesto}
              </Button>
              <p className="text-xs text-muted-foreground">Non vuoi più ricevere queste mail? Cancellati.</p>
            </div>
            <div className="space-y-2">
              <div className="flex flex-wrap gap-2">
                <Button type="button" variant="outline" onClick={() => setStep(1)}>
                  Indietro
                </Button>
                <Button type="button" variant="outline" onClick={() => testCampaign.mutate()} disabled={testCampaign.isPending}>
                  {testCampaign.isPending ? 'Un momento…' : 'Mandami una prova'}
                </Button>
                <Button type="button" onClick={() => setStep(3)}>
                  Avanti
                </Button>
              </div>
              {testCampaign.isSuccess && me.data && <p className="text-sm">Prova inviata a {me.data.email}</p>}
              <Failure message={failureMessage(testCampaign.error)} />
            </div>
          </div>
        )}
        {step === 3 && campaign && (
          <div className="max-w-md space-y-4">
            <div className="flex flex-wrap gap-2" role="group" aria-label="Quando parte">
              <Button type="button" variant={mode === 'adesso' ? 'default' : 'outline'} aria-pressed={mode === 'adesso'} onClick={() => setMode('adesso')}>
                Invia adesso
              </Button>
              <Button
                type="button"
                variant={mode === 'programma' ? 'default' : 'outline'}
                aria-pressed={mode === 'programma'}
                onClick={() => setMode('programma')}
              >
                Programma
              </Button>
            </div>
            {mode === 'programma' && (
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="campagna-giorno">Giorno</Label>
                  <Input
                    id="campagna-giorno"
                    type="date"
                    required
                    value={when.giorno}
                    onChange={(event) => setWhen({ ...when, giorno: event.target.value })}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="campagna-ora">Ora</Label>
                  <Input
                    id="campagna-ora"
                    type="time"
                    required
                    value={when.ora}
                    onChange={(event) => setWhen({ ...when, ora: event.target.value })}
                  />
                </div>
                <p className="text-xs text-muted-foreground sm:col-span-2">ora di Roma</p>
              </div>
            )}
            <div className="space-y-2">
              <div className="flex flex-wrap gap-2">
                <Button type="button" variant="outline" onClick={() => setStep(2)}>
                  Indietro
                </Button>
                <Button
                  type="button"
                  onClick={() => schedule.mutate()}
                  disabled={!campaign.pronta || schedule.isPending || (mode === 'programma' && (!when.giorno || !when.ora))}
                >
                  {schedule.isPending ? 'Un momento…' : mode === 'programma' ? 'Programma' : 'Invia'}
                </Button>
              </div>
              {!campaign.pronta && (
                <p className="text-sm text-muted-foreground">
                  Hai modificato la campagna dopo la prova: mandane un’altra dal passo Prova.
                </p>
              )}
              <Failure message={failureMessage(schedule.error)} />
            </div>
          </div>
        )}
      </div>
    </>
  )
}
