import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { Company, CompanyOverride, Freelancer, FreelancerOverride } from '@/lib/api'
import { CompanyOverrideDialog, FreelancerOverrideDialog, RecordLifecycle } from './Override'

const FREELANCER: Freelancer = {
  id: 'f1',
  first_name: 'Ada',
  last_name: 'Lovelace',
  email: 'ada@studio.it',
  linkedin_url: 'https://www.linkedin.com/in/ada',
  cv_filename: 'cv.pdf',
  cv_size: 2048,
  daily_rate: '500.00',
  job_title: 'CTO',
  remote: 'remote',
  links: ['https://github.com/ada', 'https://ada.dev'],
  status: 'active',
  origin: null,
  utm_source: null,
  utm_medium: null,
  utm_campaign: null,
  utm_content: null,
  utm_term: null,
  utm_id: null,
  phone: null,
  seniority_admin: null,
  experience_years_admin: null,
  created_at: '2026-09-10T10:00:00Z',
  filled_by: 'person',
  complete: true,
  note: [],
  logins: 3,
  provenance: 'landing',
  last_access: null,
  deleted_at: null,
  vetted_at: null,
  cv_mime: 'application/pdf',
  updated_at: '2026-09-10T10:00:00Z',
  signup_utm: null,
  last_logins: [],
  last_download_guide: [],
  pigro_slug: null,
}

const COMPANY: Company = {
  id: 'c1',
  company_name: 'Rossi Studio',
  contact_person: 'Mario Rossi',
  email: 'mario@rossi.it',
  phone: '+39 345 1234567',
  requested_role: 'Backend developer',
  project: 'Piattaforma di prenotazione',
  period_from: '2026-10-01',
  duration: '3 mesi',
  daily_budget: '450.00',
  remote: 'remote',
  onsite_days: null,
  headcount: 2,
  status: 'new',
  crm_status: null,
  crm_situation: null,
  crm_deal_url: null,
  crm_attempted_at: null,
  origin: null,
  utm_source: null,
  utm_medium: null,
  utm_campaign: null,
  utm_content: null,
  utm_term: null,
  utm_id: null,
  crm_error: null,
  created_at: '2026-09-10T10:00:00Z',
  updated_at: '2026-09-10T10:00:00Z',
  note: [],
  deleted_at: null,
  talent_cloud_grant: null,
}

function mountFreelancer({
  onSave = vi.fn(),
  onOpenChange = vi.fn(),
  saving = false,
  error = null,
}: {
  onSave?: (data: FreelancerOverride) => void
  onOpenChange?: (open: boolean) => void
  saving?: boolean
  error?: string | null
} = {}) {
  render(
    <FreelancerOverrideDialog
      freelancer={FREELANCER}
      open
      onOpenChange={onOpenChange}
      onSave={onSave}
      saving={saving}
      error={error}
    />,
  )
  return { onSave, onOpenChange }
}

function mountCompany({
  onSave = vi.fn(),
  onOpenChange = vi.fn(),
  saving = false,
  error = null,
  company = COMPANY,
}: {
  onSave?: (data: CompanyOverride) => void
  onOpenChange?: (open: boolean) => void
  saving?: boolean
  error?: string | null
  company?: Company
} = {}) {
  render(
    <CompanyOverrideDialog
      company={company}
      open
      onOpenChange={onOpenChange}
      onSave={onSave}
      saving={saving}
      error={error}
    />,
  )
  return { onSave, onOpenChange }
}

describe('FreelancerOverrideDialog', () => {
  it('pre-fills every field from the current card', () => {
    mountFreelancer()
    expect(screen.getByLabelText('Nome')).toHaveValue('Ada')
    expect(screen.getByLabelText('Cognome')).toHaveValue('Lovelace')
    expect(screen.getByLabelText('LinkedIn')).toHaveValue('https://www.linkedin.com/in/ada')
    expect(screen.getByLabelText('Posizione')).toHaveValue('CTO')
    expect(screen.getByLabelText('Tariffa a giornata (€)')).toHaveValue('500.00')
    expect(screen.getByLabelText('Link (uno per riga)')).toHaveValue(
      'https://github.com/ada\nhttps://ada.dev',
    )
    expect(screen.getByRole('combobox', { name: 'Modalità' })).toHaveTextContent('Da remoto')
    expect(screen.getByRole('combobox', { name: 'Scheda compilata da' })).toHaveTextContent('La persona')
  })

  it('sends every current value unchanged when nothing is edited', async () => {
    const onSave = vi.fn<(data: FreelancerOverride) => void>()
    mountFreelancer({ onSave })
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(onSave).toHaveBeenCalledWith({
      first_name: 'Ada',
      last_name: 'Lovelace',
      linkedin_url: 'https://www.linkedin.com/in/ada',
      daily_rate: '500.00',
      job_title: 'CTO',
      remote: 'remote',
      links: ['https://github.com/ada', 'https://ada.dev'],
      filled_by: 'person',
    })
  })

  it('clears a nullable text field to null when the box is emptied', async () => {
    const onSave = vi.fn<(data: FreelancerOverride) => void>()
    mountFreelancer({ onSave })
    await userEvent.clear(screen.getByLabelText('LinkedIn'))
    await userEvent.clear(screen.getByLabelText('Posizione'))
    await userEvent.clear(screen.getByLabelText('Tariffa a giornata (€)'))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    const sent = onSave.mock.calls[0]![0]
    expect(sent.linkedin_url).toBeNull()
    expect(sent.job_title).toBeNull()
    expect(sent.daily_rate).toBeNull()
  })

  it('sets remoto to null through the "Non impostata" option', async () => {
    const onSave = vi.fn<(data: FreelancerOverride) => void>()
    mountFreelancer({ onSave })
    await userEvent.click(screen.getByRole('combobox', { name: 'Modalità' }))
    await userEvent.click(screen.getByRole('option', { name: 'Non impostata' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(onSave.mock.calls[0]![0].remote).toBeNull()
  })

  it('splits the links textarea into a trimmed array, blank lines dropped', async () => {
    const onSave = vi.fn<(data: FreelancerOverride) => void>()
    mountFreelancer({ onSave })
    await userEvent.clear(screen.getByLabelText('Link (uno per riga)'))
    await userEvent.type(
      screen.getByLabelText('Link (uno per riga)'),
      'https://one.dev{Enter}{Enter}  https://two.dev  ',
    )
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(onSave.mock.calls[0]![0].links).toEqual(['https://one.dev', 'https://two.dev'])
  })

  it.each([
    ['1.500', '1500.00'],
    ['1.234,50', '1234.50'],
  ])('sends a rate typed as «%s» as %s, whatever the browser’s locale (REB-485)', async (typed, sent) => {
    const onSave = vi.fn<(data: FreelancerOverride) => void>()
    mountFreelancer({ onSave })
    const rate = screen.getByLabelText('Tariffa a giornata (€)')
    await userEvent.clear(rate)
    await userEvent.type(rate, typed)
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(onSave.mock.calls[0]![0].daily_rate).toBe(sent)
  })

  it('says a rate that is not an amount is not one, and keeps Save off (REB-485)', async () => {
    const onSave = vi.fn<(data: FreelancerOverride) => void>()
    mountFreelancer({ onSave })
    const rate = screen.getByLabelText('Tariffa a giornata (€)')
    await userEvent.clear(rate)
    await userEvent.type(rate, 'tanto')
    expect(screen.getByRole('alert')).toHaveTextContent('Serve una cifra, in euro.')
    expect(rate).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('button', { name: 'Salva' })).toBeDisabled()
    // 150000, over the ceiling: read as 150 it would have gone through.
    await userEvent.clear(rate)
    await userEvent.type(rate, '150.000')
    expect(screen.getByRole('button', { name: 'Salva' })).toBeDisabled()
    expect(onSave).not.toHaveBeenCalled()
  })

  it.each(['12,345', '12.3456', '0x10', '1,000.00'])(
    'refuses a rate typed as «%s» before any request (REB-485)',
    async (typed) => {
      const onSave = vi.fn<(data: FreelancerOverride) => void>()
      mountFreelancer({ onSave })
      const rate = screen.getByLabelText('Tariffa a giornata (€)')
      await userEvent.clear(rate)
      await userEvent.type(rate, typed)
      expect(screen.getByRole('alert')).toHaveTextContent('Serve una cifra, in euro.')
      expect(screen.getByRole('button', { name: 'Salva' })).toBeDisabled()
      expect(onSave).not.toHaveBeenCalled()
    },
  )

  it('disables Save when nome or cognome is blank, and never lets it through', async () => {
    const onSave = vi.fn<(data: FreelancerOverride) => void>()
    mountFreelancer({ onSave })
    await userEvent.clear(screen.getByLabelText('Nome'))
    expect(screen.getByRole('button', { name: 'Salva' })).toBeDisabled()
    expect(onSave).not.toHaveBeenCalled()
  })

  it('shows the error text and closes without saving on Annulla', async () => {
    const onOpenChange = vi.fn()
    mountFreelancer({ onOpenChange, error: 'il campo non può essere svuotato' })
    expect(screen.getByRole('alert')).toHaveTextContent('il campo non può essere svuotato')
    await userEvent.click(screen.getByRole('button', { name: 'Annulla' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('disables Save and shows "Salvo…" while saving', () => {
    mountFreelancer({ saving: true })
    expect(screen.getByRole('button', { name: 'Salvo…' })).toBeDisabled()
  })
})

describe('CompanyOverrideDialog', () => {
  it('pre-fills the seven project fields; the referente fields start blank', () => {
    mountCompany()
    expect(screen.getByLabelText('Azienda')).toHaveValue('Rossi Studio')
    expect(screen.getByLabelText('Figura richiesta')).toHaveValue('Backend developer')
    expect(screen.getByLabelText('Progetto')).toHaveValue('Piattaforma di prenotazione')
    expect(screen.getByLabelText('Da quando')).toHaveValue('2026-10-01')
    expect(screen.getByLabelText('Durata')).toHaveValue('3 mesi')
    expect(screen.getByLabelText('Budget a giornata (€)')).toHaveValue('450.00')
    expect(screen.getByLabelText('Numero di persone')).toHaveValue(2)
    expect(screen.getByRole('combobox', { name: 'Modalità' })).toHaveTextContent('Da remoto')
    expect(screen.queryByLabelText('Giorni in sede a settimana')).toBeNull()
    expect(screen.getByLabelText('Nome')).toHaveValue('')
    expect(screen.getByLabelText('Cognome')).toHaveValue('')
    expect(screen.getByLabelText('LinkedIn')).toHaveValue('')
  })

  it('shows and requires giorni in sede only for ibrido, pre-filled from the current row', () => {
    mountCompany({ company: { ...COMPANY, remote: 'hybrid', onsite_days: 3 } })
    expect(screen.getByLabelText('Giorni in sede a settimana')).toHaveValue(3)
  })

  it('omits the referente identity keys entirely when left blank', async () => {
    const onSave = vi.fn<(data: CompanyOverride) => void>()
    mountCompany({ onSave })
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    const sent = onSave.mock.calls[0]![0]
    expect(sent).toEqual({
      company_name: 'Rossi Studio',
      requested_role: 'Backend developer',
      project: 'Piattaforma di prenotazione',
      period_from: '2026-10-01',
      duration: '3 mesi',
      daily_budget: '450.00',
      remote: 'remote',
      onsite_days: null,
      headcount: 2,
    })
  })

  it('clears giorni_presenza to null the moment remoto leaves ibrido', async () => {
    const onSave = vi.fn<(data: CompanyOverride) => void>()
    mountCompany({ onSave, company: { ...COMPANY, remote: 'hybrid', onsite_days: 3 } })
    await userEvent.click(screen.getByRole('combobox', { name: 'Modalità' }))
    await userEvent.click(screen.getByRole('option', { name: 'In sede' }))
    expect(screen.queryByLabelText('Giorni in sede a settimana')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(onSave.mock.calls[0]![0]).toMatchObject({ remote: 'on_site', onsite_days: null })
  })

  it('sends giorni_presenza as a number when remoto is ibrido', async () => {
    const onSave = vi.fn<(data: CompanyOverride) => void>()
    mountCompany({ onSave })
    await userEvent.click(screen.getByRole('combobox', { name: 'Modalità' }))
    await userEvent.click(screen.getByRole('option', { name: 'Ibrido' }))
    await userEvent.type(screen.getByLabelText('Giorni in sede a settimana'), '2')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(onSave.mock.calls[0]![0]).toMatchObject({ remote: 'hybrid', onsite_days: 2 })
  })

  it('sends the referente nome/cognome only once filled in', async () => {
    const onSave = vi.fn<(data: CompanyOverride) => void>()
    mountCompany({ onSave })
    await userEvent.type(screen.getByLabelText('Nome'), 'Mario')
    await userEvent.type(screen.getByLabelText('Cognome'), 'Bianchi')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    const sent = onSave.mock.calls[0]![0]
    expect(sent.first_name).toBe('Mario')
    expect(sent.last_name).toBe('Bianchi')
    expect(sent.linkedin_url).toBeUndefined()
  })

  it('clears the referente linkedin only when the checkbox is ticked', async () => {
    const onSave = vi.fn<(data: CompanyOverride) => void>()
    mountCompany({ onSave })
    await userEvent.click(screen.getByRole('checkbox', { name: 'Rimuovi il profilo LinkedIn del referente' }))
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(onSave.mock.calls[0]![0].linkedin_url).toBeNull()
  })

  it('sends a budget typed as «1.500» as 1500, whatever the browser’s locale (REB-485)', async () => {
    const onSave = vi.fn<(data: CompanyOverride) => void>()
    mountCompany({ onSave })
    const budget = screen.getByLabelText('Budget a giornata (€)')
    await userEvent.clear(budget)
    await userEvent.type(budget, '1.500')
    await userEvent.click(screen.getByRole('button', { name: 'Salva' }))
    expect(onSave.mock.calls[0]![0].daily_budget).toBe('1500.00')
  })

  it('says a budget that is not an amount is not one, and keeps Save off (REB-485)', async () => {
    const onSave = vi.fn<(data: CompanyOverride) => void>()
    mountCompany({ onSave })
    const budget = screen.getByLabelText('Budget a giornata (€)')
    await userEvent.clear(budget)
    await userEvent.type(budget, 'tanto')
    expect(screen.getByRole('alert')).toHaveTextContent('Serve una cifra, in euro.')
    expect(budget).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('button', { name: 'Salva' })).toBeDisabled()
    expect(onSave).not.toHaveBeenCalled()
  })

  it('disables Save when a required project field is blank', async () => {
    mountCompany()
    await userEvent.clear(screen.getByLabelText('Durata'))
    expect(screen.getByRole('button', { name: 'Salva' })).toBeDisabled()
  })

  it('disables Save when ibrido has no giorni in sede yet', async () => {
    mountCompany()
    await userEvent.click(screen.getByRole('combobox', { name: 'Modalità' }))
    await userEvent.click(screen.getByRole('option', { name: 'Ibrido' }))
    expect(screen.getByRole('button', { name: 'Salva' })).toBeDisabled()
  })
})

describe('RecordLifecycle', () => {
  it('shows Elimina on a live record and calls onDelete', async () => {
    const onDelete = vi.fn()
    render(
      <RecordLifecycle
        deletedAt={null}
        deleting={false}
        restoring={false}
        onDelete={onDelete}
        onRestore={vi.fn()}
        error={null}
      />,
    )
    expect(screen.queryByText(/Eliminata/)).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Elimina' }))
    expect(onDelete).toHaveBeenCalledTimes(1)
  })

  it('shows the deleted badge and Ripristina on a deleted record, and calls onRestore', async () => {
    const onRestore = vi.fn()
    render(
      <RecordLifecycle
        deletedAt="2026-09-20T09:00:00Z"
        deleting={false}
        restoring={false}
        onDelete={vi.fn()}
        onRestore={onRestore}
        error={null}
      />,
    )
    expect(screen.getByText(/Eliminata il/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Elimina' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Ripristina' }))
    expect(onRestore).toHaveBeenCalledTimes(1)
  })

  it('shows the error line', () => {
    render(
      <RecordLifecycle
        deletedAt={null}
        deleting={false}
        restoring={false}
        onDelete={vi.fn()}
        onRestore={vi.fn()}
        error="Non riesco a completare l’operazione."
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Non riesco a completare l’operazione.')
  })
})
