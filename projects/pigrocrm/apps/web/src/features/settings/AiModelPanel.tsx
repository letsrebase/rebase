import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@rebase/ui/card'
import { Checkbox } from '@rebase/ui/checkbox'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Loader } from '@rebase/ui/loader'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { api, toProblem, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'

type AiModel = components['schemas']['AiModelRead']
type AiModelUpdate = components['schemas']['AiModelUpdate']

const PROVIDERS: ReadonlyArray<{ value: 'anthropic' | 'openai'; label: string }> = [
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'openai', label: 'OpenAI' },
]

const MISSING_LABELS: Record<string, string> = {
  provider: 'il fornitore',
  model: 'il modello',
  api_key: 'la chiave',
  daily_cap_eur: 'il limite giornaliero',
  prices: 'i due prezzi',
}

/** What the person typed over what the server holds, as text: nothing is parsed as a number. */
interface Draft {
  provider: string
  model: string
  apiKey: string
  cap: string
  inputPrice: string
  outputPrice: string
}

function draftFrom(server: AiModel): Draft {
  return {
    provider: server.provider ?? '',
    model: server.model ?? '',
    apiKey: '',
    cap: server.daily_cap_eur ?? '',
    inputPrice: server.input_eur_per_mtok ?? '',
    outputPrice: server.output_eur_per_mtok ?? '',
  }
}

/** Only what differs from the server is sent: the key goes only when one was typed. */
function changesOf(server: AiModel, draft: Draft): Partial<AiModelUpdate> {
  const change: Partial<AiModelUpdate> = {}
  if (draft.provider !== (server.provider ?? '') && draft.provider !== '') {
    change.provider = draft.provider as 'anthropic' | 'openai'
  }
  if (draft.model !== (server.model ?? '') && draft.model !== '') change.model = draft.model
  if (draft.apiKey !== '') change.api_key = draft.apiKey
  if (draft.cap !== (server.daily_cap_eur ?? '') && draft.cap !== '') change.daily_cap_eur = draft.cap
  if (draft.inputPrice !== (server.input_eur_per_mtok ?? '') && draft.inputPrice !== '') {
    change.input_eur_per_mtok = draft.inputPrice
  }
  if (draft.outputPrice !== (server.output_eur_per_mtok ?? '') && draft.outputPrice !== '') {
    change.output_eur_per_mtok = draft.outputPrice
  }
  return change
}

/**
 * Settings, Connections, Modello AI (REB-972, design 2026-11-09 § 3.2): the model PigroCRM reads
 * incoming PDFs and photos with. Off by default and an admin's: the key is written here and never
 * shown (the page learns only whether one is set), switching it on needs the settings complete
 * and the notice of what the model receives read and acknowledged, and «Prova» makes one real
 * call with what is saved. Changing the provider or the model switches it off, as the server does.
 */
export function AiModelPanel() {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: queryKeys.aiModel,
    queryFn: () => unwrap(api.GET('/api/settings/ai-model')),
  })
  const [edits, setEdits] = useState<Partial<Draft>>({})
  const [acknowledged, setAcknowledged] = useState(false)
  const [tried, setTried] = useState<string | null>(null)

  const store = (state: AiModel) => queryClient.setQueryData(queryKeys.aiModel, state)

  const save = useMutation({
    // The server's defaults (`clear_api_key`, `privacy_acknowledged`) are false: only what changes is sent.
    mutationFn: (body: Partial<AiModelUpdate>) =>
      unwrap(api.PUT('/api/settings/ai-model', { body: body as AiModelUpdate })),
    onSuccess: (state) => {
      store(state)
      setEdits({})
      setAcknowledged(false)
      toast.success('Impostazioni del modello salvate')
    },
    onError: (error) => toast.error(toProblem(error).detail),
  })

  const test = useMutation({
    mutationFn: () => unwrap(api.POST('/api/settings/ai-model/test')),
    onSuccess: (result) => {
      setTried(`Il modello ha risposto: ${result.input_tokens} token letti, ${result.output_tokens} scritti.`)
      void queryClient.invalidateQueries({ queryKey: queryKeys.aiModel })
    },
    onError: (error) => setTried(toProblem(error).detail),
  })

  if (query.isPending) return <LoadingState label="Leggo le impostazioni del modello…" />
  if (query.isError) return <QueryErrorBanner error={query.error} />

  const server = query.data
  const draft: Draft = { ...draftFrom(server), ...edits }
  const change = changesOf(server, draft)
  const dirty = Object.keys(change).length > 0
  const set = (patch: Partial<Draft>) => {
    setTried(null)
    setEdits((current) => ({ ...current, ...patch }))
  }
  const missing = server.missing.map((name) => MISSING_LABELS[name] ?? name)

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    save.mutate(change)
  }
  const turnOn = () => save.mutate({ ...change, enabled: true, privacy_acknowledged: acknowledged })
  const turnOff = () => save.mutate({ ...change, enabled: false })

  return (
    <Card>
      <CardHeader>
        <CardTitle>Modello AI</CardTitle>
        <CardDescription>
          Legge i PDF e le foto che arrivano in «In arrivo» e ne fa una proposta: la accetta o la rifiuta una persona.
          È spento finché un amministratore non lo accende.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <p className="text-sm" role="status">
          Stato: <strong>{server.enabled ? 'acceso' : 'spento'}</strong> · speso oggi {server.spent_today_eur} €
        </p>

        <form className="grid max-w-xl gap-4" onSubmit={onSubmit}>
          <div className="grid gap-1.5">
            <Label htmlFor="ai-provider">Fornitore</Label>
            <select
              id="ai-provider"
              className="h-9 border bg-background px-3 text-sm"
              value={draft.provider}
              onChange={(event) => set({ provider: event.target.value })}
            >
              <option value="">Scegli…</option>
              {PROVIDERS.map((provider) => (
                <option key={provider.value} value={provider.value}>
                  {provider.label}
                </option>
              ))}
            </select>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="ai-model">Modello</Label>
            <Input id="ai-model" value={draft.model} onChange={(event) => set({ model: event.target.value })} />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="ai-key">Chiave</Label>
            <Input
              id="ai-key"
              type="password"
              autoComplete="off"
              value={draft.apiKey}
              placeholder={server.has_key ? 'Chiave salvata: scrivine una per sostituirla' : 'Incolla la chiave'}
              onChange={(event) => set({ apiKey: event.target.value })}
            />
            <p className="text-xs text-muted-foreground">
              {server.has_key ? 'Una chiave è salvata e non viene mai mostrata.' : 'Nessuna chiave salvata.'}
            </p>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="ai-cap">Limite di spesa al giorno (€)</Label>
            <Input id="ai-cap" inputMode="decimal" value={draft.cap} onChange={(event) => set({ cap: event.target.value })} />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label htmlFor="ai-in">Prezzo per milione di token letti (€)</Label>
              <Input
                id="ai-in"
                inputMode="decimal"
                value={draft.inputPrice}
                onChange={(event) => set({ inputPrice: event.target.value })}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="ai-out">Prezzo per milione di token scritti (€)</Label>
              <Input
                id="ai-out"
                inputMode="decimal"
                value={draft.outputPrice}
                onChange={(event) => set({ outputPrice: event.target.value })}
              />
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" disabled={!dirty || save.isPending}>
              {save.isPending ? <Loader className="mr-2 size-4" /> : null}
              Salva
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={dirty || missing.length > 0 || test.isPending}
              onClick={() => {
                setTried(null)
                test.mutate()
              }}
            >
              {test.isPending ? <Loader className="mr-2 size-4" /> : null}
              Prova
            </Button>
          </div>
          {tried ? (
            <p role="status" className="text-sm">
              {tried}
            </p>
          ) : null}
        </form>

        <section aria-labelledby="ai-consent" className="max-w-xl space-y-3 border-t pt-4">
          <h3 id="ai-consent" className="font-medium">
            Cosa riceve il modello
          </h3>
          <p className="text-sm">{server.privacy_notice}</p>
          {server.enabled ? (
            <Button variant="outline" disabled={save.isPending} onClick={turnOff}>
              Spegni il modello
            </Button>
          ) : (
            <>
              {missing.length > 0 ? (
                <p className="text-sm text-muted-foreground">Per accenderlo mancano: {missing.join(', ')}.</p>
              ) : null}
              <div className="flex items-center gap-2">
                <Checkbox
                  id="ai-ack"
                  checked={acknowledged}
                  onCheckedChange={(checked) => setAcknowledged(checked === true)}
                />
                <Label htmlFor="ai-ack">Ho letto cosa riceve il modello</Label>
              </div>
              <Button disabled={!acknowledged || missing.length > 0 || dirty || save.isPending} onClick={turnOn}>
                Accendi il modello
              </Button>
            </>
          )}
        </section>
      </CardContent>
    </Card>
  )
}
