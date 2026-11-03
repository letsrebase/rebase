import { expect, test } from '@playwright/test'
import { loginAsAdmin } from './helpers'

/**
 * REB-725's definition of done, driven the way a person drives it: a task due
 * yesterday is under «In ritardo» and counted beside «Attività» in the sidebar, and
 * completing it takes it out of both. Since REB-873 «Oggi» is the first view of the
 * Tasks page, so `/app/oggi` has to land there.
 *
 * The count is compared before and after rather than against a literal, because the
 * other specs of this suite leave their own tasks in the same space.
 */
test.describe('Oggi', () => {
  test('an attività due yesterday is overdue, and Fatto takes it out of the section and the count', async ({
    page,
  }) => {
    await loginAsAdmin(page)
    const me = String((await (await page.request.get('/api/auth/me')).json()).id)
    // «Yesterday» is the server's: the panel cuts on the emitter's day, which is not the
    // runner's around midnight.
    const today = String((await (await page.request.get('/api/activities/today')).json()).today)
    const yesterday = new Date(`${today}T12:00:00Z`)
    yesterday.setUTCDate(yesterday.getUTCDate() - 1)
    const due_date = yesterday.toISOString().slice(0, 10)
    const title = `Sollecitare Acme ${Date.now()}`
    const created = await page.request.post('/api/activities', {
      data: { title, due_date, assigned_to: me },
    })
    expect(created.status(), await created.text()).toBe(201)

    await page.goto('/app/oggi')
    await expect(page).toHaveURL(/\/app\/activities\?view=today/)
    await expect(page.getByRole('tab', { name: 'Oggi' })).toHaveAttribute('aria-selected', 'true')
    const overdue = page
      .getByRole('heading', { name: /In ritardo/ })
      .locator('xpath=ancestor::section')
    await expect(overdue.getByText(title)).toBeVisible()

    const badge = page.getByRole('link', { name: /^Attività\s*\d+ in ritardo$/ }).locator('span[aria-hidden]')
    await expect(badge).toBeVisible()
    const before = Number(((await badge.textContent()) ?? '').trim())

    await overdue
      .getByRole('listitem')
      .filter({ hasText: title })
      .getByRole('button', { name: 'Fatto' })
      .click()

    await expect(overdue.getByText(title)).toHaveCount(0)
    if (before === 1) {
      await expect(badge).toHaveCount(0)
    } else {
      await expect(badge).toHaveText(String(before - 1))
    }
  })
})
