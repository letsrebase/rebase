import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { useState, type FormEvent, type ReactNode } from 'react'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Textarea } from '@rebase/ui/textarea'
import { AMOUNT_PROBLEM, euroAmount, sentAmount } from '@/lib/amount'
import { ApiError, admin, type AdminCompanyRequest, type Remoto } from '@/lib/api'
import { dealIdOf } from '@/lib/crm'
import { Header } from './lists'

const REMOTO_WORDS: Record<Remoto, string> = {
  remoto: 'Da remoto',
  ibrido: 'Ibrido',
  in_sede: 'In sede',
}

const EMPTY = {
  nome_azienda: '',
  referente_nome: '',
  referente_cognome: '',
  email: '',
  telefono: '',
  figura_richiesta: '',
  progetto: '',
  periodo_da: '',
  durata: '',
  budget_giornaliero: '',
  remoto: '' as Remoto | '',
  giorni_presenza: '',
  numero_risorse: '1',
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
export function AdminCreaAzienda() {
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
      void queryClient.invalidateQueries({ queryKey: ['companies'] })
      void navigate({ to: '/admin/companies/$id', params: { id: created.id } })
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    const budget = euroAmount(form.budget_giornaliero)
    if (!Number.isFinite(budget)) return setProblem(`Budget giornaliero: ${AMOUNT_PROBLEM}`)
    if (!form.remoto) return setProblem('Scegli la modalità di lavoro.')
    if (form.remoto === 'ibrido' && !form.giorni_presenza) return setProblem('Indica i giorni in sede a settimana.')
    const deal = form.deal.trim() ? dealIdOf(form.deal) : undefined
    if (deal === null) return setProblem('Il deal non è un id né l’indirizzo di un deal del CRM.')
    setProblem(null)
    create.mutate({
      nome_azienda: form.nome_azienda.trim(),
      referente_nome: form.referente_nome.trim(),
      referente_cognome: form.referente_cognome.trim(),
      email: form.email.trim(),
      telefono: form.telefono.trim(),
      figura_richiesta: form.figura_richiesta.trim(),
      progetto: form.progetto.trim(),
      periodo_da: form.periodo_da,
      durata: form.durata.trim(),
      budget_giornaliero: sentAmount(form.budget_giornaliero),
      remoto: form.remoto,
      giorni_presenza: form.remoto === 'ibrido' ? form.giorni_presenza : '',
      numero_risorse: form.numero_risorse,
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
          <Field id="nuova-azienda" label="Azienda">
            <Input id="nuova-azienda" required value={form.nome_azienda} onChange={(e) => set('nome_azienda')(e.target.value)} />
          </Field>
          <Field id="nuova-figura" label="Figura richiesta">
            <Input id="nuova-figura" required value={form.figura_richiesta} onChange={(e) => set('figura_richiesta')(e.target.value)} />
          </Field>
          <Field id="nuova-nome" label="Nome del referente">
            <Input id="nuova-nome" required value={form.referente_nome} onChange={(e) => set('referente_nome')(e.target.value)} />
          </Field>
          <Field id="nuova-cognome" label="Cognome del referente">
            <Input id="nuova-cognome" required value={form.referente_cognome} onChange={(e) => set('referente_cognome')(e.target.value)} />
          </Field>
          <Field id="nuova-email" label="Email">
            <Input id="nuova-email" type="email" required value={form.email} onChange={(e) => set('email')(e.target.value)} />
          </Field>
          <Field id="nuova-telefono" label="Telefono">
            <Input id="nuova-telefono" type="tel" required value={form.telefono} onChange={(e) => set('telefono')(e.target.value)} />
          </Field>
        </div>
        <Field id="nuova-progetto" label="Progetto">
          <Textarea id="nuova-progetto" required rows={5} value={form.progetto} onChange={(e) => set('progetto')(e.target.value)} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field id="nuova-inizio" label="Inizio desiderato">
            <Input id="nuova-inizio" type="date" required value={form.periodo_da} onChange={(e) => set('periodo_da')(e.target.value)} />
          </Field>
          <Field id="nuova-durata" label="Durata">
            <Input id="nuova-durata" required value={form.durata} onChange={(e) => set('durata')(e.target.value)} />
          </Field>
          <Field id="nuova-budget" label="Budget giornaliero (€)">
            <Input id="nuova-budget" inputMode="decimal" required value={form.budget_giornaliero} onChange={(e) => set('budget_giornaliero')(e.target.value)} />
          </Field>
          <Field id="nuova-risorse" label="Persone richieste">
            <Input id="nuova-risorse" type="number" min={1} required value={form.numero_risorse} onChange={(e) => set('numero_risorse')(e.target.value)} />
          </Field>
          <Field id="nuova-remoto" label="Modalità">
            <Select value={form.remoto} onValueChange={(value) => set('remoto')(value as Remoto)}>
              <SelectTrigger id="nuova-remoto">
                <SelectValue placeholder="Scegli" />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(REMOTO_WORDS).map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          {form.remoto === 'ibrido' && (
            <Field id="nuova-giorni" label="Giorni in sede a settimana">
              <Input id="nuova-giorni" type="number" min={1} max={5} required value={form.giorni_presenza} onChange={(e) => set('giorni_presenza')(e.target.value)} />
            </Field>
          )}
        </div>
        <Field
          id="nuova-deal"
          label="Deal nel CRM"
          hint="Se l’azienda è già nel CRM con un deal aperto, incolla il suo indirizzo o il suo id: la richiesta si collega a quel deal e il CRM non crea né un secondo cliente né un secondo deal. Senza, la richiesta viene registrata come deal nuovo."
        >
          <Input id="nuova-deal" placeholder="…/app/deal/…" value={form.deal} onChange={(e) => set('deal')(e.target.value)} />
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
