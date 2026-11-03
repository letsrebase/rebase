import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { useState, type FormEvent, type ReactNode } from 'react'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Textarea } from '@rebase/ui/textarea'
import { AMOUNT_PROBLEM, euroAmount, sentAmount } from '@/lib/amount'
import { ApiError, admin, type AdminCompanyRequest, type Remote } from '@/lib/api'
import { dealIdOf } from '@/lib/crm'
import { keys } from '@/lib/query-keys'
import { Header } from './list-kit'

const RemoteWords: Record<Remote, string> = {
  remote: 'Da remoto',
  hybrid: 'Ibrido',
  on_site: 'In sede',
}

const EMPTY = {
  company_name: '',
  contact_first_name: '',
  contact_last_name: '',
  email: '',
  phone: '',
  requested_role: '',
  project: '',
  period_from: '',
  duration: '',
  daily_budget: '',
  remote: '' as Remote | '',
  onsite_days: '',
  headcount: '1',
  deal: '',
}

type Form = typeof EMPTY

function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

/** «Nuova richiesta» (REB-870, spec 2026-10-30 § 6): an admin opens a request on the
 *  referente's behalf for a company met directly, with the wizard's fields and, when the CRM
 *  already has the deal, its address or id. No mail goes to anybody. With a deal the CRM
 *  links it and creates nothing; without one it is filed as a new deal. */
export function AdminCreateCompany() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [form, setForm] = useState<Form>(EMPTY)
  const [problem, setProblem] = useState<string | null>(null)

  function set<K extends keyof Form>(key: K) {
    return (value: Form[K]) => setForm((current) => ({ ...current, [key]: value }))
  }

  const create = useMutation({
    mutationFn: (data: AdminCompanyRequest) => admin.createCompany(data),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: keys.companies() })
      void navigate({ to: '/admin/companies/$id', params: { id: created.id } })
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    const budget = euroAmount(form.daily_budget)
    if (!Number.isFinite(budget)) return setProblem(`Budget giornaliero: ${AMOUNT_PROBLEM}`)
    if (!form.remote) return setProblem('Scegli la modalità di lavoro.')
    if (form.remote === 'hybrid' && !form.onsite_days) return setProblem('Indica i giorni in sede a settimana.')
    const deal = form.deal.trim() ? dealIdOf(form.deal) : undefined
    if (deal === null) return setProblem('Il deal non è un id né l’indirizzo di un deal del CRM.')
    setProblem(null)
    create.mutate({
      company_name: form.company_name.trim(),
      contact_first_name: form.contact_first_name.trim(),
      contact_last_name: form.contact_last_name.trim(),
      email: form.email.trim(),
      phone: form.phone.trim(),
      requested_role: form.requested_role.trim(),
      project: form.project.trim(),
      period_from: form.period_from,
      duration: form.duration.trim(),
      daily_budget: sentAmount(form.daily_budget),
      remote: form.remote,
      onsite_days: form.remote === 'hybrid' ? form.onsite_days : '',
      headcount: form.headcount,
      ...(deal ? { deal_id: deal } : {}),
    })
  }

  const failure =
    create.error instanceof ApiError
      ? create.error.message
      : create.error
        ? 'Non riesco ad aprire la richiesta.'
        : null

  return (
    <>
      <Header title="Nuova richiesta">
        <Button asChild variant="outline" size="sm">
          <Link to="/admin/companies">Torna alle aziende</Link>
        </Button>
      </Header>
      <form onSubmit={submit} className="max-w-3xl space-y-6 px-6 py-6">
        <p className="text-sm text-muted-foreground">
          Apri la richiesta di un’azienda incontrata direttamente. Non parte nessuna mail, né al referente né a
          rebase.
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field id="new-company" label="Azienda">
            <Input id="new-company" required value={form.company_name} onChange={(e) => set('company_name')(e.target.value)} />
          </Field>
          <Field id="new-role" label="Figura richiesta">
            <Input id="new-role" required value={form.requested_role} onChange={(e) => set('requested_role')(e.target.value)} />
          </Field>
          <Field id="new-first-name" label="Nome del referente">
            <Input id="new-first-name" required value={form.contact_first_name} onChange={(e) => set('contact_first_name')(e.target.value)} />
          </Field>
          <Field id="new-last-name" label="Cognome del referente">
            <Input id="new-last-name" required value={form.contact_last_name} onChange={(e) => set('contact_last_name')(e.target.value)} />
          </Field>
          <Field id="new-email" label="Email">
            <Input id="new-email" type="email" required value={form.email} onChange={(e) => set('email')(e.target.value)} />
          </Field>
          <Field id="new-phone" label="Telefono">
            <Input id="new-phone" type="tel" required value={form.phone} onChange={(e) => set('phone')(e.target.value)} />
          </Field>
        </div>
        <Field id="new-project" label="Progetto">
          <Textarea id="new-project" required rows={5} value={form.project} onChange={(e) => set('project')(e.target.value)} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field id="new-start" label="Inizio desiderato">
            <Input id="new-start" type="date" required value={form.period_from} onChange={(e) => set('period_from')(e.target.value)} />
          </Field>
          <Field id="new-duration" label="Durata">
            <Input id="new-duration" required value={form.duration} onChange={(e) => set('duration')(e.target.value)} />
          </Field>
          <Field id="new-budget" label="Budget giornaliero (€)">
            <Input id="new-budget" inputMode="decimal" required value={form.daily_budget} onChange={(e) => set('daily_budget')(e.target.value)} />
          </Field>
          <Field id="new-headcount" label="Persone richieste">
            <Input id="new-headcount" type="number" min={1} required value={form.headcount} onChange={(e) => set('headcount')(e.target.value)} />
          </Field>
          <Field id="new-remote" label="Modalità">
            <Select value={form.remote} onValueChange={(value) => set('remote')(value as Remote)}>
              <SelectTrigger id="new-remote">
                <SelectValue placeholder="Scegli" />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(RemoteWords).map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          {form.remote === 'hybrid' && (
            <Field id="new-days" label="Giorni in sede a settimana">
              <Input id="new-days" type="number" min={1} max={5} required value={form.onsite_days} onChange={(e) => set('onsite_days')(e.target.value)} />
            </Field>
          )}
        </div>
        <Field
          id="new-deal"
          label="Deal nel CRM"
          hint="Se l’azienda è già nel CRM con un deal aperto, incolla il suo indirizzo o il suo id: la richiesta si collega a quel deal e il CRM non crea né un secondo cliente né un secondo deal. Senza, la richiesta viene registrata come deal nuovo."
        >
          <Input id="new-deal" placeholder="…/app/deal/…" value={form.deal} onChange={(e) => set('deal')(e.target.value)} />
        </Field>
        {(problem ?? failure) && (
          <p role="alert" className="text-sm text-destructive">
            {problem ?? failure}
          </p>
        )}
        <Button type="submit" disabled={create.isPending}>
          {create.isPending ? 'Apro la richiesta…' : 'Apri la richiesta'}
        </Button>
      </form>
    </>
  )
}
