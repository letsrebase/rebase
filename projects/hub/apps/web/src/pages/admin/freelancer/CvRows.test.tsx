import { cleanup, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { CV_PERSON, mountDetail, resetMocks, valueOf } from '../list-test-kit'

afterEach(resetMocks)

describe('the CV’s data in the profile card (REB-855)', () => {
  it('shows the CV’s phone marked «dal CV» when the profile has none', async () => {
    await mountDetail({ phone: null }, { person: CV_PERSON, differences: [] })
    expect(await screen.findByText('+39 333 123 4567 · dal CV')).toBeInTheDocument()
  })

  it('shows the profile’s phone alone when the CV states the same number, however it is spaced', async () => {
    await mountDetail({ phone: '+393331234567' }, { person: CV_PERSON, differences: [] })
    await screen.findByText('Aziende e clienti')
    expect(valueOf('Telefono')).toHaveTextContent(/^\+393331234567$/)
  })

  it('shows both phones, the CV’s on a second line, when they differ', async () => {
    await mountDetail({ phone: '02 5550199' }, { person: CV_PERSON, differences: [] })
    await screen.findByText('Aziende e clienti')
    const phone = valueOf('Telefono')
    expect(phone).toHaveTextContent('02 5550199')
    expect(within(phone).getByText(/\+39 333 123 4567/)).toHaveTextContent('+39 333 123 4567 · dal CV')
  })

  it('shows the CV’s email only when it is not the account’s, ignoring case', async () => {
    await mountDetail({}, { person: CV_PERSON, differences: [] })
    expect(await screen.findByText('Email nel CV')).toBeInTheDocument()
    expect(within(valueOf('Email nel CV')).getByRole('link')).toHaveAttribute('href', 'mailto:grace.cv@example.org')
    cleanup()
    await mountDetail({}, { person: { ...CV_PERSON, email: 'GRACE@studio.it' }, differences: [] })
    await screen.findByText('Aziende e clienti')
    expect(screen.queryByText('Email nel CV')).toBeNull()
  })

  it('shows the CV’s city marked «dal CV», and no row without one', async () => {
    await mountDetail({}, { person: CV_PERSON, differences: [] })
    expect(await screen.findByText('Torino · dal CV')).toBeInTheDocument()
    cleanup()
    await mountDetail({}, { person: { ...CV_PERSON, city: null }, differences: [] })
    await screen.findByText('Aziende e clienti')
    expect(screen.queryByText('Città')).toBeNull()
  })

  it('falls back to the CV’s LinkedIn only when the profile has none', async () => {
    await mountDetail({ linkedin_url: null }, { person: CV_PERSON, differences: [] })
    await screen.findByText('Aziende e clienti')
    const row = valueOf('LinkedIn')
    expect(within(row).getByRole('link')).toHaveAttribute('href', 'https://www.linkedin.com/in/grace-cv')
    expect(row).toHaveTextContent('· dal CV')
    cleanup()
    await mountDetail({}, { person: CV_PERSON, differences: [] })
    await screen.findByText('Aziende e clienti')
    const own = valueOf('LinkedIn')
    expect(within(own).getByRole('link')).toHaveAttribute('href', 'https://www.linkedin.com/in/ada')
    expect(own).not.toHaveTextContent('dal CV')
  })

  it('lists the profile’s links, then the CV’s not already among them, marked «dal CV»', async () => {
    await mountDetail({}, { person: CV_PERSON, differences: [] })
    await screen.findByText('Aziende e clienti')
    const items = within(valueOf('Link')).getAllByRole('listitem')
    expect(items.map((item) => item.textContent)).toEqual([
      'https://github.com/ada',
      'https://github.com/grace-cv · dal CV',
    ])
  })

  it('shows the companies and clients the CV names, marked «dal CV», and no row without any', async () => {
    await mountDetail({}, { person: CV_PERSON, differences: [] })
    expect(await screen.findByText('Acme Pagamenti, Bottega Digitale · dal CV')).toBeInTheDocument()
    cleanup()
    await mountDetail({}, { person: { ...CV_PERSON, companies: [] }, differences: [] })
    await screen.findByText('Torino · dal CV')
    expect(screen.queryByText('Aziende e clienti')).toBeNull()
  })

  it('warns at the top of the card where the CV and the profile disagree, and keeps names to the alert', async () => {
    await mountDetail(
      {},
      {
        person: CV_PERSON,
        differences: [
          { field: 'last_name', cv: 'Hoppers', profile: 'Hopper' },
          { field: 'email', cv: 'grace.cv@example.org', profile: 'grace@studio.it' },
        ],
      },
    )
    const alert = await screen.findByRole('alert')
    expect(within(alert).getByText('Il CV e il profilo non coincidono')).toBeInTheDocument()
    expect(within(alert).getByText(/Cognome: nel CV «Hoppers», nel profilo «Hopper»/)).toBeInTheDocument()
    expect(within(alert).getByText(/Email: nel CV «grace.cv@example.org», nel profilo «grace@studio.it»/)).toBeInTheDocument()
    expect(screen.queryByText('Nome', { selector: 'dt' })).toBeNull()
  })

  it('shows no alert when nothing differs', async () => {
    await mountDetail({}, { person: CV_PERSON, differences: [] })
    await screen.findByText('Aziende e clienti')
    expect(screen.queryByText('Il CV e il profilo non coincidono')).toBeNull()
  })

  it('shows only an http(s) CV address as a link', async () => {
    await mountDetail({}, { person: { ...CV_PERSON, other_links: ['javascript:alert(1)'] }, differences: [] })
    expect(await screen.findByText('javascript:alert(1) · dal CV')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'javascript:alert(1)' })).toBeNull()
  })

  it('says «Rigenera scheda» reads the CV for a card without the data, when there is a CV', async () => {
    await mountDetail({}, { person: null })
    expect(await screen.findByText('Dati dal CV non ancora letti: «Rigenera scheda» li legge.')).toBeInTheDocument()
    expect(screen.queryByText('Aziende e clienti')).toBeNull()
    cleanup()
    await mountDetail({ cv_filename: null, cv_size: null }, { person: null })
    await screen.findByText('Nessuna scheda: manca il CV.')
    expect(screen.queryByText(/Dati dal CV non ancora letti/)).toBeNull()
  })
})
