import { useState, type FormEvent } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Textarea } from '@rebase/ui/textarea'
import { ContactPointsEditor } from '@/components/ContactPointsEditor'
import { AMOUNT_PROBLEM, acceptedAmount, sentAmount } from '@/lib/amount'
import type { Company, CompanyOverride, Freelancer, FreelancerOverride, Remote } from '@/lib/api'
import { allValid, draftsFrom, toWrite, type PointDraft } from '@/lib/contactPoints'
import { REMOTE_LABELS, formatDate } from '@/lib/format'

// A `Select` needs a non-empty string; `remote` is the one override field that is both
// nullable and rendered as a `Select`, so it alone needs a sentinel for "not set".
const UNSET = '__non_impostata__'

interface FreelancerOverrideDraft {
  first_name: string
  last_name: string
  linkedinUrl: string
  dailyRate: string
  job_title: string
  remote: Remote | typeof UNSET
  links: string
  filledBy: 'person' | 'admin'
  contacts: PointDraft[]
}

function draftFromFreelancer(f: Freelancer): FreelancerOverrideDraft {
  return {
    first_name: f.first_name,
    last_name: f.last_name,
    linkedinUrl: f.linkedin_url ?? '',
    dailyRate: f.daily_rate ?? '',
    job_title: f.job_title ?? '',
    remote: f.remote ?? UNSET,
    links: f.links.join('\n'),
    filledBy: f.filled_by,
    contacts: draftsFrom(f.contact_points),
  }
}

/**
 * The admin's own edit form for a freelancer card, beyond `status`/`note`
 * (`StatusEditor`'s own field): every field `FreelancerOverride` names, pre-filled with
 * the card's current values. `first_name`/`last_name`/`links` are never nullable columns, so
 * they always carry a value; the rest read as «—» on the card when cleared, which is
 * exactly what an emptied box here sends (`null`), the same convention the read view
 * already uses. Stays mounted across opens (`DealForm`'s own reasoning): `Dialog`'s
 * `open` prop alone toggles visibility, so the draft resets on the `open` transition
 * rather than a `useState` initializer that would only ever run once.
 */
export function FreelancerOverrideDialog({
  freelancer,
  open,
  onOpenChange,
  onSave,
  saving,
  error,
}: {
  freelancer: Freelancer
  open: boolean
  onOpenChange: (open: boolean) => void
  onSave: (data: FreelancerOverride) => void
  saving: boolean
  error: string | null
}) {
  const [draft, setDraft] = useState<FreelancerOverrideDraft>(() => draftFromFreelancer(freelancer))
  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) setDraft(draftFromFreelancer(freelancer))
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    onSave({
      first_name: draft.first_name.trim(),
      last_name: draft.last_name.trim(),
      linkedin_url: draft.linkedinUrl.trim() || null,
      daily_rate: sentAmount(draft.dailyRate) || null,
      job_title: draft.job_title.trim() || null,
      remote: draft.remote === UNSET ? null : draft.remote,
      links: draft.links
        .split('\n')
        .map((link) => link.trim())
        .filter(Boolean),
      filled_by: draft.filledBy,
      contact_points: toWrite(draft.contacts),
    })
  }

  // Blank is allowed here: it clears the rate.
  const rateProblem = draft.dailyRate.trim() !== '' && !acceptedAmount(draft.dailyRate)
  const valid =
    draft.first_name.trim() !== '' &&
    draft.last_name.trim() !== '' &&
    !rateProblem &&
    allValid(draft.contacts)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Modifica scheda</DialogTitle>
          <DialogDescription>
            Oltre stato e note, già gestiti sopra. Un campo svuotato torna a «—»; ogni
            valore sostituito o svuotato entra nel registro delle modifiche qui sotto,
            con quello che c&apos;era prima.
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={submit}>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="override-freelancer-first-name">Nome</Label>
              <Input
                id="override-freelancer-first-name"
                value={draft.first_name}
                onChange={(event) => setDraft({ ...draft, first_name: event.target.value })}
                required
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="override-freelancer-last-name">Cognome</Label>
              <Input
                id="override-freelancer-last-name"
                value={draft.last_name}
                onChange={(event) => setDraft({ ...draft, last_name: event.target.value })}
                required
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="override-freelancer-linkedin">LinkedIn</Label>
            <Input
              id="override-freelancer-linkedin"
              value={draft.linkedinUrl}
              onChange={(event) => setDraft({ ...draft, linkedinUrl: event.target.value })}
              placeholder="Vuoto per rimuoverlo"
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="override-freelancer-job-title">Posizione</Label>
              <Input
                id="override-freelancer-job-title"
                value={draft.job_title}
                onChange={(event) => setDraft({ ...draft, job_title: event.target.value })}
                placeholder="Vuoto per rimuoverla"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="override-freelancer-rate">Tariffa a giornata (€)</Label>
              <Input
                id="override-freelancer-rate"
                inputMode="decimal"
                value={draft.dailyRate}
                onChange={(event) => setDraft({ ...draft, dailyRate: event.target.value })}
                placeholder="Vuoto per rimuoverla"
                aria-invalid={rateProblem || undefined}
                aria-describedby={rateProblem ? 'override-freelancer-rate-error' : undefined}
              />
              {rateProblem && (
                <p role="alert" id="override-freelancer-rate-error" className="text-sm text-destructive">
                  {AMOUNT_PROBLEM}
                </p>
              )}
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="override-freelancer-remote">Modalità</Label>
            <Select
              value={draft.remote}
              onValueChange={(value) => setDraft({ ...draft, remote: value as Remote | typeof UNSET })}
            >
              <SelectTrigger id="override-freelancer-remote">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={UNSET}>Non impostata</SelectItem>
                {Object.entries(REMOTE_LABELS).map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="override-freelancer-links">Link (uno per riga)</Label>
            <Textarea
              id="override-freelancer-links"
              rows={3}
              value={draft.links}
              onChange={(event) => setDraft({ ...draft, links: event.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="override-freelancer-filled-by">Scheda compilata da</Label>
            <Select
              value={draft.filledBy}
              onValueChange={(value) => setDraft({ ...draft, filledBy: value as 'person' | 'admin' })}
            >
              <SelectTrigger id="override-freelancer-filled-by">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="person">La persona</SelectItem>
                <SelectItem value="admin">L&apos;admin</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <fieldset className="space-y-3 border p-4">
            <legend className="px-1 text-sm font-medium">Recapiti</legend>
            <ContactPointsEditor
              idPrefix="override-freelancer-contact"
              login={freelancer.email}
              drafts={draft.contacts}
              onChange={(contacts) => setDraft({ ...draft, contacts })}
            />
          </fieldset>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Annulla
            </Button>
            <Button type="submit" disabled={!valid || saving}>
              {saving ? 'Salvo…' : 'Salva'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

interface CompanyOverrideDraft {
  companyName: string
  requestedRole: string
  project: string
  periodFrom: string
  duration: string
  dailyBudget: string
  remote: Remote
  onsiteDays: string
  headcount: string
  contactFirstName: string
  contactLastName: string
  contactPersonLinkedin: string
  clearContactPersonLinkedin: boolean
  contacts: PointDraft[]
}

function draftFromCompany(c: Company): CompanyOverrideDraft {
  return {
    companyName: c.company_name,
    requestedRole: c.requested_role,
    project: c.project,
    periodFrom: c.period_from,
    duration: c.duration,
    dailyBudget: c.daily_budget,
    remote: c.remote,
    onsiteDays: c.onsite_days !== null ? String(c.onsite_days) : '',
    headcount: String(c.headcount),
    // `CompanyRead` carries the referente as one combined `contact_person` string, never the
    // separate `first_name`/`last_name`/`linkedin_url` `CompanyOverride` takes (those live on
    // the linked `users` row, REB-281) -- so there is no current value to pre-fill,
    // and these three start blank: left alone, they are simply not sent.
    contactFirstName: '',
    contactLastName: '',
    contactPersonLinkedin: '',
    clearContactPersonLinkedin: false,
    contacts: draftsFrom(c.contact_points),
  }
}

/**
 * Same contract as `FreelancerOverrideDialog`, for a `Company` request: the seven
 * project answers (REB-314; REB-380 adds `remote`/`onsite_days`/
 * `headcount`/`requested_role`) and the company's own name are always known
 * and always sent -- `onsite_days` clears itself to `null` the moment `remote`
 * leaves `hybrid`, matching the wizard's own conditional field and the database's
 * own together-`CHECK`. The referente's identity is admin-only and not part of
 * `CompanyRead` at all, so those three fields start blank and are only sent when
 * the admin actually fills one in, or ticks «Rimuovi il profilo LinkedIn» to clear
 * it explicitly.
 */
export function CompanyOverrideDialog({
  company,
  open,
  onOpenChange,
  onSave,
  saving,
  error,
}: {
  company: Company
  open: boolean
  onOpenChange: (open: boolean) => void
  onSave: (data: CompanyOverride) => void
  saving: boolean
  error: string | null
}) {
  const [draft, setDraft] = useState<CompanyOverrideDraft>(() => draftFromCompany(company))
  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) setDraft(draftFromCompany(company))
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    const payload: CompanyOverride = {
      company_name: draft.companyName.trim(),
      requested_role: draft.requestedRole.trim(),
      project: draft.project.trim(),
      period_from: draft.periodFrom,
      duration: draft.duration.trim(),
      daily_budget: sentAmount(draft.dailyBudget),
      remote: draft.remote,
      onsite_days: draft.remote === 'hybrid' ? Number(draft.onsiteDays) : null,
      headcount: Number(draft.headcount),
      contact_points: toWrite(draft.contacts),
    }
    if (draft.contactFirstName.trim()) payload.first_name = draft.contactFirstName.trim()
    if (draft.contactLastName.trim()) payload.last_name = draft.contactLastName.trim()
    if (draft.clearContactPersonLinkedin) payload.linkedin_url = null
    else if (draft.contactPersonLinkedin.trim()) payload.linkedin_url = draft.contactPersonLinkedin.trim()
    onSave(payload)
  }

  const budgetProblem = draft.dailyBudget.trim() !== '' && !acceptedAmount(draft.dailyBudget)
  const valid =
    !budgetProblem &&
    draft.companyName.trim() !== '' &&
    draft.requestedRole.trim() !== '' &&
    draft.project.trim() !== '' &&
    draft.periodFrom !== '' &&
    draft.duration.trim() !== '' &&
    draft.dailyBudget.trim() !== '' &&
    draft.headcount.trim() !== '' &&
    (draft.remote !== 'hybrid' || draft.onsiteDays.trim() !== '') &&
    allValid(draft.contacts)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Modifica richiesta</DialogTitle>
          <DialogDescription>
            Oltre stato e note, già gestiti sopra. Ogni valore sostituito entra nel
            registro delle modifiche qui sotto, con quello che c&apos;era prima.
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={submit}>
          <div className="space-y-1.5">
            <Label htmlFor="override-company-company-name">Azienda</Label>
            <Input
              id="override-company-company-name"
              value={draft.companyName}
              onChange={(event) => setDraft({ ...draft, companyName: event.target.value })}
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="override-company-requested-role">Figura richiesta</Label>
            <Input
              id="override-company-requested-role"
              value={draft.requestedRole}
              onChange={(event) => setDraft({ ...draft, requestedRole: event.target.value })}
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="override-company-project">Progetto</Label>
            <Textarea
              id="override-company-project"
              rows={4}
              value={draft.project}
              onChange={(event) => setDraft({ ...draft, project: event.target.value })}
              required
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="override-company-period-from">Da quando</Label>
              <Input
                id="override-company-period-from"
                type="date"
                value={draft.periodFrom}
                onChange={(event) => setDraft({ ...draft, periodFrom: event.target.value })}
                required
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="override-company-duration">Durata</Label>
              <Input
                id="override-company-duration"
                value={draft.duration}
                onChange={(event) => setDraft({ ...draft, duration: event.target.value })}
                required
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="override-company-budget">Budget a giornata (€)</Label>
            <Input
              id="override-company-budget"
              inputMode="decimal"
              value={draft.dailyBudget}
              onChange={(event) => setDraft({ ...draft, dailyBudget: event.target.value })}
              required
              aria-invalid={budgetProblem || undefined}
              aria-describedby={budgetProblem ? 'override-company-budget-error' : undefined}
            />
            {budgetProblem && (
              <p role="alert" id="override-company-budget-error" className="text-sm text-destructive">
                {AMOUNT_PROBLEM}
              </p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="override-company-headcount">Numero di persone</Label>
            <Input
              id="override-company-headcount"
              type="number"
              min="1"
              step="1"
              value={draft.headcount}
              onChange={(event) => setDraft({ ...draft, headcount: event.target.value })}
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="override-company-remote">Modalità</Label>
            <Select
              value={draft.remote}
              onValueChange={(value) =>
                setDraft({
                  ...draft,
                  remote: value as Remote,
                  onsiteDays: value === 'hybrid' ? draft.onsiteDays : '',
                })
              }
            >
              <SelectTrigger id="override-company-remote">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(REMOTE_LABELS).map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {draft.remote === 'hybrid' && (
            <div className="space-y-1.5">
              <Label htmlFor="override-company-onsite-days">Giorni in sede a settimana</Label>
              <Input
                id="override-company-onsite-days"
                type="number"
                min="1"
                max="4"
                step="1"
                value={draft.onsiteDays}
                onChange={(event) => setDraft({ ...draft, onsiteDays: event.target.value })}
                required
              />
            </div>
          )}
          <fieldset className="space-y-3 border p-3">
            <legend className="px-1 text-sm font-medium">Referente</legend>
            <p className="text-sm text-muted-foreground">
              Vuoto per non modificarlo: la scheda non mostra il nome e il cognome
              separati, solo «{company.contact_person}».
            </p>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="override-company-contact-first-name">Nome</Label>
                <Input
                  id="override-company-contact-first-name"
                  value={draft.contactFirstName}
                  onChange={(event) => setDraft({ ...draft, contactFirstName: event.target.value })}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="override-company-contact-last-name">Cognome</Label>
                <Input
                  id="override-company-contact-last-name"
                  value={draft.contactLastName}
                  onChange={(event) => setDraft({ ...draft, contactLastName: event.target.value })}
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="override-company-contact-linkedin">LinkedIn</Label>
              <Input
                id="override-company-contact-linkedin"
                value={draft.contactPersonLinkedin}
                disabled={draft.clearContactPersonLinkedin}
                onChange={(event) => setDraft({ ...draft, contactPersonLinkedin: event.target.value })}
              />
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                id="override-company-contact-clear-linkedin"
                checked={draft.clearContactPersonLinkedin}
                onCheckedChange={(checked) =>
                  setDraft({ ...draft, clearContactPersonLinkedin: checked === true })
                }
              />
              <Label htmlFor="override-company-contact-clear-linkedin" className="font-normal">
                Rimuovi il profilo LinkedIn del referente
              </Label>
            </div>
          </fieldset>
          <fieldset className="space-y-3 border p-4">
            <legend className="px-1 text-sm font-medium">Recapiti del referente</legend>
            <ContactPointsEditor
              idPrefix="override-company-contact"
              login={company.email}
              drafts={draft.contacts}
              onChange={(contacts) => setDraft({ ...draft, contacts })}
            />
          </fieldset>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Annulla
            </Button>
            <Button type="submit" disabled={!valid || saving}>
              {saving ? 'Salvo…' : 'Salva'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

/**
 * The delete/restore control for the record's header, beside its state pill (REB-355):
 * one click either way, the same "reversible, so no confirmation" convention
 * `Admins.tsx`'s own demote button already keeps -- `soft_delete`/`restore` exist
 * precisely so an admin's own mistake here costs one more click, not a support ticket.
 */
export function RecordLifecycle({
  deletedAt,
  deleting,
  restoring,
  onDelete,
  onRestore,
  error,
}: {
  deletedAt: string | null
  deleting: boolean
  restoring: boolean
  onDelete: () => void
  onRestore: () => void
  error: string | null
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {deletedAt !== null ? (
        <>
          <Badge variant="pill">Eliminata il {formatDate(deletedAt)}</Badge>
          <Button type="button" variant="outline" size="sm" onClick={onRestore} disabled={restoring}>
            {restoring ? 'Ripristino…' : 'Ripristina'}
          </Button>
        </>
      ) : (
        <Button type="button" variant="destructive" size="sm" onClick={onDelete} disabled={deleting}>
          {deleting ? 'Elimino…' : 'Elimina'}
        </Button>
      )}
      {error && (
        <p role="alert" className="w-full text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}
