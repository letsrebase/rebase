import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState, type FormEvent } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import {
  ApiError,
  admin,
  type AiConfig,
  type AiProva,
  type AiProvider,
  type AiSourceSwitch,
  type AiStaleCards,
  type AiTask,
} from '@/lib/api'
import { formatDateTime, formatEuro } from '@/lib/format'
import { Empty, Header } from './lists'

const KEY = ['ai-config'] as const
const PROVIDER_LABELS: Record<string, string> = { anthropic: 'Anthropic', openai: 'OpenAI', google: 'Google' }
const TASK_LABELS: Record<string, string> = { card: 'Scheda anonima', team_proposal: 'Proposta di team' }
const SOURCE_LABELS: Record<string, string> = { github: 'GitHub', links: 'Altri link' }
const STALE_KEY = ['ai-stale-cards'] as const
const BATCH_REFRESH_MS = 30_000
const START_REFRESH_MS = 3_000
const START_WATCH_MS = 60_000
const EFFORTS = ['low', 'medium', 'high'] as const
const EFFORT_LABELS: Record<string, string> = { low: 'Basso', medium: 'Medio', high: 'Alto' }
// A `Select` needs a non-empty string; «no thinking» is the one nullable effort.
const NO_EFFORT = 'none'
const NO_SECRETS_KEY =
  'Manca REBASE_SECRETS_KEY: senza, le chiavi non si possono salvare né leggere. Configurala sul server e riavvia.'

// A probe costs a fraction of a cent: `formatEuro`'s two places would show it as 0,00 €.
const smallEuro = new Intl.NumberFormat('it-IT', {
  style: 'currency',
  currency: 'EUR',
  minimumFractionDigits: 2,
  maximumFractionDigits: 6,
})
function formatSmallEuro(value: string): string {
  return smallEuro.format(Number(value))
}

function problem(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

function useInvalidate() {
  const client = useQueryClient()
  return () => client.invalidateQueries({ queryKey: KEY })
}

export function AdminModelliAi() {
  const config = useQuery({ queryKey: KEY, queryFn: () => admin.aiConfig() })
  return (
    <div>
      <Header title="Modelli AI" />
      {config.isError && (
        <p role="alert" className="px-6 py-4 text-sm text-destructive">
          Non riesco a leggere la configurazione.
        </p>
      )}
      {config.data && <Sections config={config.data} />}
    </div>
  )
}

function Sections({ config }: { config: AiConfig }) {
  const locked = !config.secrets_key_present
  return (
    <>
      {locked && (
        <p role="alert" className="border-b bg-muted px-6 py-4 text-sm text-destructive">
          {NO_SECRETS_KEY}
        </p>
      )}
      <section aria-labelledby="ai-chiavi" className="border-b">
        <h2 id="ai-chiavi" className="px-6 pt-6 text-lg font-semibold">
          Chiavi
        </h2>
        <p className="px-6 pt-1 text-sm text-muted-foreground">
          Una chiave si scrive e basta: dopo il salvataggio si vedono solo le ultime quattro cifre.
        </p>
        <div className="divide-y">
          {config.providers.map((provider) => (
            <ProviderRow key={provider.provider} provider={provider} locked={locked} />
          ))}
        </div>
      </section>
      <section aria-labelledby="ai-fonti" className="border-b">
        <h2 id="ai-fonti" className="px-6 pt-6 text-lg font-semibold">
          Fonti
        </h2>
        <p className="px-6 pt-1 text-sm text-muted-foreground">
          Oltre al CV, la scheda può leggere ciò che la persona ha indicato. Ogni fonte parte spenta.
        </p>
        <div className="divide-y">
          {config.sources.map((source) => (
            <SourceRow key={source.kind} source={source} />
          ))}
        </div>
      </section>
      <section aria-labelledby="ai-aggiornare" className="border-b">
        <h2 id="ai-aggiornare" className="px-6 pt-6 text-lg font-semibold">
          Schede da aggiornare
        </h2>
        <StaleCards />
      </section>
      <section aria-labelledby="ai-compiti" className="border-b">
        <h2 id="ai-compiti" className="px-6 pt-6 text-lg font-semibold">
          Compiti
        </h2>
        <div className="divide-y">
          {config.tasks.map((task) => (
            <TaskForm key={task.task} task={task} config={config} />
          ))}
        </div>
      </section>
      <section aria-labelledby="ai-prezzi" className="border-b">
        <h2 id="ai-prezzi" className="px-6 pt-6 text-lg font-semibold">
          Prezzi
        </h2>
        <Prices config={config} />
      </section>
      <section aria-labelledby="ai-spesa">
        <h2 id="ai-spesa" className="px-6 pt-6 text-lg font-semibold">
          Spesa del mese
          <span className="ml-2 text-base font-normal text-muted-foreground">{config.spend.month}</span>
        </h2>
        <Spend config={config} />
      </section>
    </>
  )
}

function SourceRow({ source }: { source: AiSourceSwitch }) {
  const invalidate = useInvalidate()
  const client = useQueryClient()
  const label = SOURCE_LABELS[source.kind] ?? source.kind
  const toggle = useMutation({
    mutationFn: () => admin.aiSetSourceEnabled(source.kind, !source.enabled),
    // Turning a source off deletes its stored text, and the cards that read it turn stale:
    // the list below, its count and its cost are read again too.
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: STALE_KEY })
      void invalidate()
    },
  })
  return (
    <div className="flex flex-wrap items-center gap-4 px-6 py-5">
      <div className="min-w-40 space-y-1">
        <p className="font-medium">{label}</p>
        <Badge variant={source.enabled ? 'default' : 'outline'}>{source.enabled ? 'Accesa' : 'Spenta'}</Badge>
      </div>
      <Button
        type="button"
        variant="outline"
        role="switch"
        aria-checked={source.enabled}
        aria-label={label}
        disabled={toggle.isPending}
        onClick={() => toggle.mutate()}
      >
        {source.enabled ? 'Spegni' : 'Accendi'}
      </Button>
      <div className="space-y-1 text-sm text-muted-foreground">
        {!source.enabled && <p>Spenta finché la privacy non è aggiornata</p>}
        {source.updated_at && source.updated_by_name && (
          <p className="text-xs">
            Cambiata da {source.updated_by_name}, {formatDateTime(source.updated_at)}
          </p>
        )}
      </div>
      {toggle.isError && (
        <p role="alert" className="w-full text-sm text-destructive">
          {problem(toggle.error, 'Non riesco a salvare.')}
        </p>
      )}
    </div>
  )
}

const REASON_LABELS: Record<string, string> = {
  prompt: 'per versione del prompt',
  source: 'per fonti cambiate',
}

function StaleCards() {
  const client = useQueryClient()
  const [limit, setLimit] = useState('5')
  const start = useMutation({
    mutationFn: (n: number) => admin.aiRegenerateStale(n),
    onSuccess: () => client.invalidateQueries({ queryKey: STALE_KEY }),
  })
  // The batch's row is written after the response, so the first read after a start can miss
  // it: keep reading for a short while after a start that queued cards, and for as long as a
  // batch is on. The `cards` service applies it, not this page.
  const stale = useQuery({
    queryKey: STALE_KEY,
    queryFn: () => admin.aiStaleCards(),
    refetchInterval: (query) => {
      if (query.state.data?.batch) return BATCH_REFRESH_MS
      const justStarted = start.isSuccess && start.data.queued > 0 && Date.now() - start.submittedAt < START_WATCH_MS
      return justStarted ? START_REFRESH_MS : false
    },
  })
  if (stale.isError) {
    return (
      <p role="alert" className="px-6 py-4 text-sm text-destructive">
        Non riesco a leggere le schede da aggiornare.
      </p>
    )
  }
  if (!stale.data) return <p className="px-6 py-4 text-sm text-muted-foreground">Caricamento…</p>
  const data: AiStaleCards = stale.data
  const queued = start.isSuccess && (
    <p role="status" className="px-6 pt-4 text-sm">
      {start.data.queued === 0
        ? 'Una rigenerazione è già in corso: attendi che finisca prima di avviarne un’altra.'
        : `${start.data.queued} ${start.data.queued === 1 ? 'scheda messa' : 'schede messe'} in coda: si scrivono in background.`}
    </p>
  )
  const running = data.batch && (
    <p role="status" className="px-6 pt-4 text-sm">
      Rigenerazione in corso col Batch API: {data.batch.count} {data.batch.count === 1 ? 'scheda' : 'schede'}
      {data.batch.submitted_at ? ` inviate il ${formatDateTime(data.batch.submitted_at)}` : ' in invio'}, {data.batch.done}{' '}
      {data.batch.done === 1 ? 'applicata' : 'applicate'}. Di solito finisce entro un’ora: la pagina si aggiorna da sola.
    </p>
  )
  if (data.count === 0) {
    return (
      <>
        {queued}
        {running}
        <Empty>Nessuna scheda da aggiornare</Empty>
      </>
    )
  }
  const n = Number(limit)
  const invalid = limit.trim() === '' || !Number.isInteger(n) || n < 1 || n > 50
  const reasons = (['prompt', 'source'] as const).filter((reason) => (data.by_reason[reason] ?? 0) > 0)
  function submit(event: FormEvent) {
    event.preventDefault()
    if (!invalid) start.mutate(n)
  }
  return (
    <div>
      {queued}
      {running}
      <div className="space-y-4 px-6 py-5">
        <div className="space-y-1 text-sm">
          <p>
            <span className="font-medium">{data.count}</span> {data.count === 1 ? 'scheda' : 'schede'}
            {reasons.length > 0 && (
              <span className="text-muted-foreground">
                {' '}
                ({reasons.map((reason) => `${data.by_reason[reason]} ${REASON_LABELS[reason]}`).join(', ')})
              </span>
            )}
          </p>
          <p className="text-muted-foreground">
            Costo stimato:{' '}
            {data.estimated_cost_eur === null ? 'stima non disponibile' : formatSmallEuro(data.estimated_cost_eur)}
          </p>
        </div>
        <ul className="space-y-1 text-sm">
          {data.items.map((item) => (
            <li key={item.freelancer_id} className="flex flex-wrap gap-x-3">
              <Link to="/admin/freelance/$id" params={{ id: item.freelancer_id }} className="font-medium hover:underline">
                {item.nome || 'Senza nome'}
              </Link>
              <span className="text-muted-foreground">{REASON_LABELS[item.reason] ?? item.reason}</span>
              {item.generated_at && (
                <span className="text-muted-foreground">scritta il {formatDateTime(item.generated_at)}</span>
              )}
            </li>
          ))}
        </ul>
        {!data.batch && (
          <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
            <div className="space-y-2">
              <Label htmlFor="stale-limit">Quante</Label>
              <Input
                id="stale-limit"
                type="number"
                min={1}
                max={50}
                className="w-24"
                value={limit}
                aria-invalid={invalid}
                onChange={(event) => setLimit(event.target.value)}
              />
            </div>
            <Button type="submit" disabled={start.isPending || invalid}>
              {start.isPending ? 'Avvio…' : 'Avvia rigenerazione'}
            </Button>
          </form>
        )}
        {!data.batch && (
          <p className="text-sm text-muted-foreground">
            Da {data.batch_threshold} schede in su la rigenerazione passa dal Batch API: costa la metà e finisce, di
            solito, entro un’ora. Sotto, si scrivono subito, in parallelo.
          </p>
        )}
        {invalid && <p className="text-sm text-destructive">Un numero intero da 1 a 50.</p>}
        {start.isError && (
          <p role="alert" className="text-sm text-destructive">
            {problem(start.error, 'Non riesco ad avviare la rigenerazione.')}
          </p>
        )}
      </div>
    </div>
  )
}

function ProviderRow({ provider, locked }: { provider: AiProvider; locked: boolean }) {
  const invalidate = useInvalidate()
  const [key, setKey] = useState('')
  const save = useMutation({
    mutationFn: () => admin.aiSaveKey(provider.provider, key),
    onSuccess: () => {
      setKey('')
      void invalidate()
    },
  })
  const toggle = useMutation({
    mutationFn: () => admin.aiSetEnabled(provider.provider, !provider.enabled),
    onSuccess: () => void invalidate(),
  })
  const id = `key-${provider.provider}`
  const label = PROVIDER_LABELS[provider.provider] ?? provider.provider

  function submit(event: FormEvent) {
    event.preventDefault()
    if (key.trim()) save.mutate()
  }

  return (
    <form onSubmit={submit} className="grid gap-4 px-6 py-5 sm:grid-cols-[10rem_minmax(0,1fr)_auto_auto] sm:items-end">
      <div className="space-y-1">
        <p className="font-medium">{label}</p>
        <Badge variant={provider.enabled ? 'default' : 'outline'}>{provider.enabled ? 'Acceso' : 'Spento'}</Badge>
      </div>
      <div className="space-y-2">
        <Label htmlFor={id}>Chiave {label}</Label>
        <Input
          id={id}
          type="password"
          autoComplete="off"
          value={key}
          disabled={locked}
          placeholder={provider.configured ? 'Scrivi per sostituirla' : 'Incolla la chiave'}
          onChange={(event) => setKey(event.target.value)}
        />
      </div>
      {/* The hint sits in its own row under the field so «items-end» lines the buttons up with the input, not with this line. */}
      <p className="-mt-2 text-xs text-muted-foreground sm:col-span-3 sm:col-start-2 sm:row-start-2">
        {provider.key_hint ?? 'non configurata'}
        {provider.saved_at && provider.saved_by_name
          ? ` · salvata da ${provider.saved_by_name}, ${formatDateTime(provider.saved_at)}`
          : ''}
      </p>
      <Button type="submit" className="sm:w-32" disabled={locked || save.isPending || !key.trim()}>
        {save.isPending ? 'Salvo…' : 'Salva chiave'}
      </Button>
      <Button
        type="button"
        variant="outline"
        className="sm:w-24"
        disabled={locked || toggle.isPending || (!provider.configured && !provider.enabled)}
        onClick={() => toggle.mutate()}
      >
        {provider.enabled ? 'Spegni' : 'Accendi'}
      </Button>
      {(save.isError || toggle.isError) && (
        <p role="alert" className="text-sm text-destructive sm:col-span-4 sm:row-start-3">
          {problem(save.error ?? toggle.error, 'Non riesco a salvare.')}
        </p>
      )}
    </form>
  )
}

function TaskForm({ task, config }: { task: AiTask; config: AiConfig }) {
  const invalidate = useInvalidate()
  const [provider, setProvider] = useState(task.provider)
  const [model, setModel] = useState(task.model)
  const [effort, setEffort] = useState(task.effort ?? NO_EFFORT)
  const [maxTokens, setMaxTokens] = useState(String(task.max_output_tokens))
  const [cap, setCap] = useState(task.daily_cap_eur ?? '')
  const [enabled, setEnabled] = useState(task.enabled)
  const [prova, setProva] = useState<AiProva | null>(null)

  const save = useMutation({
    mutationFn: () =>
      admin.aiUpdateTask(task.task, {
        provider,
        model: model.trim(),
        effort: effort === NO_EFFORT ? null : effort,
        max_output_tokens: Number(maxTokens),
        daily_cap_eur: cap.trim() === '' ? null : cap.trim(),
        enabled,
      }),
    onSuccess: () => void invalidate(),
  })
  const probe = useMutation({
    mutationFn: () => admin.aiProva(task.task),
    onSuccess: (result) => {
      setProva(result)
      void invalidate()
    },
  })
  const id = `task-${task.task}`
  const suggestions = config.known_models[provider] ?? []

  function submit(event: FormEvent) {
    event.preventDefault()
    save.mutate()
  }

  return (
    <form onSubmit={submit} className="grid gap-4 px-6 py-5 sm:grid-cols-3">
      <div className="space-y-1 sm:col-span-3">
        <p className="font-medium">
          {TASK_LABELS[task.task] ?? task.task}
          <span className="ml-2 text-sm font-normal text-muted-foreground">
            oggi {formatEuro(task.spent_today_eur)}
            {task.daily_cap_eur !== null ? ` su ${formatEuro(task.daily_cap_eur)}` : ''}
          </span>
        </p>
        {task.reason && (
          <p role="status" className="text-sm text-destructive">
            {task.reason}
          </p>
        )}
      </div>
      <div className="space-y-2">
        <Label htmlFor={`${id}-provider`}>Fornitore</Label>
        <Select value={provider} onValueChange={setProvider}>
          <SelectTrigger id={`${id}-provider`} className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {config.providers.map((p) => (
              <SelectItem key={p.provider} value={p.provider}>
                {PROVIDER_LABELS[p.provider] ?? p.provider}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-2">
        <Label htmlFor={`${id}-model`}>Modello</Label>
        <Input
          id={`${id}-model`}
          list={`${id}-models`}
          value={model}
          onChange={(event) => setModel(event.target.value)}
        />
        <datalist id={`${id}-models`}>
          {suggestions.map((name) => (
            <option key={name} value={name} />
          ))}
        </datalist>
      </div>
      <div className="space-y-2">
        <Label htmlFor={`${id}-effort`}>Ragionamento</Label>
        <Select value={effort} onValueChange={setEffort}>
          <SelectTrigger id={`${id}-effort`} className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NO_EFFORT}>Nessuno</SelectItem>
            {EFFORTS.map((level) => (
              <SelectItem key={level} value={level}>
                {EFFORT_LABELS[level]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-2">
        <Label htmlFor={`${id}-tokens`}>Massimo di token in uscita</Label>
        <Input
          id={`${id}-tokens`}
          type="number"
          min={1}
          step={1}
          value={maxTokens}
          onChange={(event) => setMaxTokens(event.target.value)}
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor={`${id}-cap`}>Tetto giornaliero, €</Label>
        <Input
          id={`${id}-cap`}
          type="number"
          min={0}
          step="0.01"
          placeholder="Nessun tetto"
          value={cap}
          onChange={(event) => setCap(event.target.value)}
        />
      </div>
      <div className="flex items-end gap-2">
        <Button
          type="button"
          variant="outline"
          aria-pressed={enabled}
          onClick={() => setEnabled(!enabled)}
        >
          {enabled ? 'Compito acceso' : 'Compito spento'}
        </Button>
      </div>
      <div className="flex flex-wrap items-center gap-2 sm:col-span-3">
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? 'Salvo…' : 'Salva compito'}
        </Button>
        <Button type="button" variant="outline" disabled={probe.isPending} onClick={() => probe.mutate()}>
          {probe.isPending ? 'Provo…' : 'Prova'}
        </Button>
        {prova && !prova.error && (
          <p role="status" className="text-sm">
            Risposta: <span className="font-medium">{prova.answer}</span> · {prova.latency_ms} ms ·{' '}
            {prova.cost_eur !== null ? formatSmallEuro(prova.cost_eur) : 'senza prezzo'}
          </p>
        )}
        {prova?.error && (
          <p role="alert" className="text-sm text-destructive">
            {prova.error}
          </p>
        )}
        {(save.isError || probe.isError) && (
          <p role="alert" className="text-sm text-destructive">
            {problem(save.error ?? probe.error, 'Non riesco a completare l’operazione.')}
          </p>
        )}
      </div>
    </form>
  )
}

function Prices({ config }: { config: AiConfig }) {
  const invalidate = useInvalidate()
  const [provider, setProvider] = useState('anthropic')
  const [model, setModel] = useState('')
  const [input, setInput] = useState('')
  const [output, setOutput] = useState('')
  const save = useMutation({
    mutationFn: () =>
      admin.aiUpsertPrice({
        provider,
        model: model.trim(),
        input_eur_per_mtok: input,
        output_eur_per_mtok: output,
        thinking_eur_per_mtok: null,
        cache_read_eur_per_mtok: null,
      }),
    onSuccess: () => {
      setModel('')
      setInput('')
      setOutput('')
      void invalidate()
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    save.mutate()
  }

  return (
    <>
      {config.prices.length === 0 ? (
        <Empty>Nessun prezzo: un modello senza prezzo non può essere chiamato.</Empty>
      ) : (
        <Table scrollLabel="Prezzi dei modelli">
          <TableHeader>
            <TableRow>
              <TableHead>Fornitore</TableHead>
              <TableHead>Modello</TableHead>
              <TableHead>Ingresso, € / Mtok</TableHead>
              <TableHead>Uscita, € / Mtok</TableHead>
              <TableHead>Ragionamento, € / Mtok</TableHead>
              <TableHead>Cache, € / Mtok</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {config.prices.map((price) => (
              <TableRow key={`${price.provider}/${price.model}`}>
                <TableCell>{PROVIDER_LABELS[price.provider] ?? price.provider}</TableCell>
                <TableCell>{price.model}</TableCell>
                <TableCell>{price.input_eur_per_mtok}</TableCell>
                <TableCell>{price.output_eur_per_mtok}</TableCell>
                <TableCell>{price.thinking_eur_per_mtok ?? 'come uscita'}</TableCell>
                <TableCell>{price.cache_read_eur_per_mtok ?? 'come ingresso'}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <form onSubmit={submit} className="grid gap-4 px-6 py-5 sm:grid-cols-[1fr_1fr_1fr_1fr_auto] sm:items-end">
        {/* A grid, not «space-y-2»: inside a form Radix adds a hidden native select after the trigger, so the trigger would take that margin and sit 8px above its neighbours. */}
        <div className="grid gap-2">
          <Label htmlFor="price-provider">Fornitore del prezzo</Label>
          <Select value={provider} onValueChange={setProvider}>
            <SelectTrigger id="price-provider" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {config.providers.map((p) => (
                <SelectItem key={p.provider} value={p.provider}>
                  {PROVIDER_LABELS[p.provider] ?? p.provider}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="price-model">Modello del prezzo</Label>
          <Input id="price-model" value={model} onChange={(event) => setModel(event.target.value)} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="price-input">Ingresso, € per milione di token</Label>
          <Input
            id="price-input"
            type="number"
            min={0}
            step="0.0001"
            value={input}
            onChange={(event) => setInput(event.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="price-output">Uscita, € per milione di token</Label>
          <Input
            id="price-output"
            type="number"
            min={0}
            step="0.0001"
            value={output}
            onChange={(event) => setOutput(event.target.value)}
          />
        </div>
        <Button type="submit" disabled={save.isPending || !model.trim() || input === '' || output === ''}>
          {save.isPending ? 'Salvo…' : 'Salva prezzo'}
        </Button>
        {save.isError && (
          <p role="alert" className="text-sm text-destructive sm:col-span-5">
            {problem(save.error, 'Non riesco a salvare il prezzo.')}
          </p>
        )}
      </form>
    </>
  )
}

function Spend({ config }: { config: AiConfig }) {
  if (config.spend.rows.length === 0) return <Empty>Nessuna chiamata questo mese.</Empty>
  return (
    <Table scrollLabel="Spesa del mese">
      <TableHeader>
        <TableRow>
          <TableHead>Compito</TableHead>
          <TableHead>Fornitore</TableHead>
          <TableHead>Spesa</TableHead>
          <TableHead>Chiamate</TableHead>
          <TableHead>Senza prezzo</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {config.spend.rows.map((row) => (
          <TableRow key={`${row.task}/${row.provider}`}>
            <TableCell>{TASK_LABELS[row.task] ?? row.task}</TableCell>
            <TableCell>{PROVIDER_LABELS[row.provider] ?? row.provider}</TableCell>
            <TableCell>{formatEuro(row.cost_eur)}</TableCell>
            <TableCell>{row.calls}</TableCell>
            <TableCell>{row.unpriced_calls}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
