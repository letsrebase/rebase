import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CARD_TALENT, LEAD_TALENT, LINKED_PAIR, NAME_PAIR, answer, cellUnder, mount, resetMocks, settle } from '../list-test-kit'

afterEach(resetMocks)

describe('the Talenti list (REB-282/283)', () => {
  it('lists a card and a lead together, each with its own state and origin, and links to the right page', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { total: 2, items: [CARD_TALENT, LEAD_TALENT], by_status: { new: 1, lead: 1 } }),
    )
    mount('/admin/talent')

    const ada = (await screen.findByText('ada@studio.it')).closest('tr')!
    expect(within(cellUnder(ada, 'Stato')).getByText('Nuovo')).toBeInTheDocument()
    expect(cellUnder(ada, 'Provenienza')).toHaveTextContent('wizard')
    const adaLink = within(ada).getByRole('link', { name: 'Ada Lovelace' })
    expect(adaLink.getAttribute('href')).toMatch(/\/admin\/freelance\/f1$/)
    // REB-558: a card's own day rate, work mode and LinkedIn link.
    expect(cellUnder(ada, 'Tariffa')).toHaveTextContent('450')
    expect(cellUnder(ada, 'Modalità')).toHaveTextContent('Da remoto')
    const adaLinkedin = within(ada).getByRole('link', { name: 'LinkedIn di Ada Lovelace' })
    expect(adaLinkedin.getAttribute('href')).toBe('https://www.linkedin.com/in/ada')
    expect(adaLinkedin.getAttribute('target')).toBe('_blank')
    expect(adaLinkedin.getAttribute('rel')).toBe('noreferrer')

    const bob = screen.getByText('bob@example.org').closest('tr')!
    expect(within(cellUnder(bob, 'Stato')).getByText('Lead')).toBeInTheDocument()
    expect(cellUnder(bob, 'Provenienza')).toHaveTextContent('form')
    const bobLink = within(bob).getByRole('link', { name: 'Bob Ross' })
    expect(bobLink.getAttribute('href')).toMatch(/\/admin\/talent\/s2$/)
    // REB-558: a lead has no rate and no mode, but keeps its LinkedIn link.
    expect(cellUnder(bob, 'Tariffa')).toHaveTextContent('—')
    expect(cellUnder(bob, 'Modalità')).toHaveTextContent('—')
    expect(within(bob).getByRole('link', { name: 'LinkedIn di Bob Ross' })).toBeInTheDocument()

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('2')
  })

  it('shows a row\'s role, level, years, place and first skills from the anonymous card (REB-823)', async () => {
    const llm = {
      ...CARD_TALENT,
      role: 'AI engineer',
      seniority: 'senior',
      experience_years: 9,
      location: 'Milano',
      skills: ['LLM', 'Python', 'RAG', 'Docker', 'AWS', 'Terraform'],
    }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { total: 2, items: [llm, LEAD_TALENT], by_status: { new: 1, lead: 1 } }),
    )
    mount('/admin/talent')

    const ada = (await screen.findByText('ada@studio.it')).closest('tr')!
    expect(cellUnder(ada, 'Profilo')).toHaveTextContent('AI engineer')
    expect(cellUnder(ada, 'Profilo')).toHaveTextContent('Senior · 9 anni · Milano')
    expect(cellUnder(ada, 'Competenze')).toHaveTextContent('LLM, Python, RAG, Docker, AWS')
    expect(cellUnder(ada, 'Competenze')).not.toHaveTextContent('Terraform')
    const bob = screen.getByText('bob@example.org').closest('tr')!
    expect(cellUnder(bob, 'Profilo')).toHaveTextContent('—')
    expect(cellUnder(bob, 'Competenze')).toHaveTextContent('—')
  })

  it('reads the LinkedIn cell as «—» for anything other than an http(s) address (REB-558)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, {
        total: 1,
        items: [{ ...CARD_TALENT, linkedin_url: 'javascript:alert(1)' }],
        by_status: { new: 1 },
      }),
    )
    mount('/admin/talent')

    const ada = (await screen.findByText('ada@studio.it')).closest('tr')!
    expect(cellUnder(ada, 'LinkedIn')).toHaveTextContent('—')
    expect(within(ada).queryByRole('link', { name: /LinkedIn/ })).not.toBeInTheDocument()
  })

  it('reads the Modalità cell as «—» for a stored value outside the three known ones (REB-558)', async () => {
    // `TalentRead.remote` is `str | None` on the API, not narrowed to `Remote`: a
    // stray value must not render blank.
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, {
        total: 1,
        items: [{ ...CARD_TALENT, remote: 'boh' }],
        by_status: { new: 1 },
      }),
    )
    mount('/admin/talent')

    const ada = (await screen.findByText('ada@studio.it')).closest('tr')!
    expect(cellUnder(ada, 'Modalità')).toHaveTextContent('—')
  })

  it('filters by state through the same pills as before, «Lead» included', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { total: 0, items: [], by_status: {} }))
    mount('/admin/talent')
    await screen.findByRole('heading', { name: 'Talenti' })
    expect(screen.getByRole('button', { name: 'Nuovo' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Lead' })).toBeInTheDocument()
  })
})

describe('the search box debounces before it reaches the API and the URL (REB-286)', () => {
  it('waits for a pause in typing before searching Talenti', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { total: 0, items: [], by_status: {} }),
    )
    mount('/admin/talent')
    await screen.findByRole('heading', { name: 'Talenti' })
    spy.mockClear()

    await userEvent.type(screen.getByLabelText('Cerca'), 'ada')
    await settle()

    const calls = spy.mock.calls.map((call) => String(call[0])).filter((url) => url.includes('/api/hub/talent'))
    expect(calls).toHaveLength(1)
    expect(calls[0]).toContain('q=ada')
  })
})

describe('every filter and the search box live in the URL, both ways (REB-286)', () => {
  it('reflects a state pill and a filter field into the address for Talenti', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { total: 0, items: [], by_status: {} }))
    const router = mount('/admin/talent')
    await screen.findByRole('heading', { name: 'Talenti' })

    await userEvent.click(screen.getByRole('button', { name: 'Nuovo' }))
    await userEvent.type(screen.getByLabelText('Posizione'), 'CTO')

    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({ status: 'new', job_title: 'CTO' }),
    )
  })

  it('reads a filter and a state back out of a URL a link already carries, for Talenti', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { total: 0, items: [], by_status: {} }))
    mount('/admin/talent?job_title=CTO&status=new')
    await screen.findByRole('heading', { name: 'Talenti' })
    expect(screen.getByLabelText('Posizione')).toHaveValue('CTO')
  })

  it('sends the page filter of Talenti as origin_page, never as origin, which is the row channel (REB-798)', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(answer(200, { total: 0, items: [], by_status: {} }))
    const router = mount('/admin/talent')
    await screen.findByRole('heading', { name: 'Talenti' })

    await userEvent.type(screen.getByLabelText('Pagina di provenienza'), 'home')

    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({ origin_page: 'home' }),
    )
    await waitFor(() => {
      const urls = fetch.mock.calls.map(([url]) => String(url))
      expect(urls.some((url) => url.includes('origin_page=home'))).toBe(true)
      expect(urls.some((url) => /[?&]origin=/.test(url))).toBe(false)
    })
  })

  it('sends the senior LLM profiles under 400 a day, cheapest first, as the API takes them (REB-823)', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(answer(200, { total: 0, items: [], by_status: {} }))
    mount('/admin/talent?skills=LLM%2C%20Python&seniority=senior&max_rate=400&min_years=5&sort=rate_asc')
    await screen.findByRole('heading', { name: 'Talenti' })

    await waitFor(() => {
      const url = new URL(
        String(fetch.mock.calls.map(([input]) => input).find((input) => String(input).includes('/api/hub/talent'))),
        'http://test',
      )
      expect(url.searchParams.getAll('skills')).toEqual(['LLM', 'Python'])
      expect(url.searchParams.getAll('seniority')).toEqual(['senior'])
      expect(url.searchParams.get('max_rate')).toBe('400.00')
      expect(url.searchParams.get('min_years')).toBe('5')
      expect(url.searchParams.get('sort')).toBe('rate_asc')
    })
    expect(screen.getByLabelText('Competenze (separate da virgole)')).toHaveValue('LLM, Python')
  })

  it('does not send years it cannot read (REB-823)', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(answer(200, { total: 0, items: [], by_status: {} }))
    mount('/admin/talent?min_years=molti')
    await screen.findByRole('heading', { name: 'Talenti' })
    expect(fetch.mock.calls.every(([url]) => !String(url).includes('min_years'))).toBe(true)
  })

  it('keeps a purely numeric filter value on a fresh load, not just an in-app navigation', async () => {
    // A digit-only value in the URL must read back as the same text on a shared link or
    // a reload, never only on an in-app navigate(): the router's parseSearch keeps an
    // amount filter's raw text, and strParam coerces any other parameter's number back.
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { total: 0, items: [], by_status: {} }))
    mount('/admin/talent?min_rate=50')
    await screen.findByRole('heading', { name: 'Talenti' })
    expect(screen.getByLabelText('Tariffa min (€/giorno)')).toHaveValue('50')
  })
})

describe('infinite scroll walks the cursor, a page at a time (REB-286)', () => {
  it('shows a second page of Talenti after «Mostra altri», cursor included in the request', async () => {
    const page1 = { total: 2, items: [CARD_TALENT], by_status: { new: 1 }, next_cursor: 'CURSOR1' }
    const page2 = { total: 2, items: [LEAD_TALENT], by_status: { new: 1, lead: 1 }, next_cursor: null }
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = new URL(String(input), 'http://test')
      return answer(200, url.searchParams.get('cursor') === 'CURSOR1' ? page2 : page1)
    })
    mount('/admin/talent')

    await screen.findByText('ada@studio.it')
    expect(screen.queryByText('bob@example.org')).toBeNull()
    expect(screen.getByText('Mostrati 1 talenti, ce ne sono altri.')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Mostra altri' }))

    await screen.findByText('bob@example.org')
    expect(screen.getByText('ada@studio.it')).toBeInTheDocument()
    expect(spy.mock.calls.some((call) => String(call[0]).includes('cursor=CURSOR1'))).toBe(true)
  })
})

describe('two empty states, in Italian (REB-286)', () => {
  it('says the Talenti table itself is empty with no filter active', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { total: 0, items: [], by_status: {} }))
    mount('/admin/talent')
    expect(await screen.findByText('Nessun profilo qui.')).toBeInTheDocument()
  })

  it('names the filters when one narrows Talenti to nothing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { total: 0, items: [], by_status: {} }))
    mount('/admin/talent?job_title=Astrofisico')
    expect(await screen.findByText('Nessun risultato per questi filtri.')).toBeInTheDocument()
  })
})

describe('«Possibile duplicato» and the way into «Duplicati» (REB-800)', () => {
  it('flags a row whose card may be someone else, linking to that card', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, {
        total: 2,
        items: [{ ...CARD_TALENT, duplicate_possible_of: ['f2'] }, { ...LEAD_TALENT, duplicate_possible_of: [] }],
        by_status: {},
      }),
    )
    mount('/admin/talent')
    const ada = (await screen.findByText('ada@studio.it')).closest('tr')!
    expect(within(ada).getByRole('link', { name: 'Possibile duplicato' })).toHaveAttribute(
      'href',
      '/admin/freelance/f2',
    )
    const bob = screen.getByText('bob@example.org').closest('tr')!
    expect(within(bob).queryByText('Possibile duplicato')).toBeNull()
  })

  it('sends a row with several look-alikes to the page that lists the pairs', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { total: 1, items: [{ ...CARD_TALENT, duplicate_possible_of: ['f2', 'f3'] }], by_status: {} }),
    )
    mount('/admin/talent')
    expect(await screen.findByRole('link', { name: 'Possibile duplicato' })).toHaveAttribute(
      'href',
      '/admin/talent/duplicates',
    )
  })

  it('links to «Duplicati» with the number of pairs, and only when there is one', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { total: 1, items: [{ ...CARD_TALENT, duplicate_possible_of: [] }], by_status: {} }),
    )
    mount('/admin/talent', { mergeable: [LINKED_PAIR], to_review: [NAME_PAIR], signups: 0 })
    expect(await screen.findByRole('link', { name: 'Duplicati · 2' })).toHaveAttribute(
      'href',
      '/admin/talent/duplicates',
    )
  })

  it('shows no link when nobody is on the list twice', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { total: 1, items: [{ ...CARD_TALENT, duplicate_possible_of: [] }], by_status: {} }),
    )
    mount('/admin/talent')
    await screen.findByText('ada@studio.it')
    await settle()
    expect(screen.queryByRole('link', { name: /Duplicati/ })).toBeNull()
  })

  it('leaves the list alone when the pairs cannot be read', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { total: 1, items: [{ ...CARD_TALENT, duplicate_possible_of: [] }], by_status: {} }),
    )
    mount('/admin/talent', () => answer(500, { detail: 'boom' }))
    expect(await screen.findByText('ada@studio.it')).toBeInTheDocument()
    await settle()
    expect(screen.queryByRole('link', { name: /Duplicati/ })).toBeNull()
  })
})
