import { expect, test } from '@playwright/test'
import { loginAsAdmin } from './helpers'

/**
 * REB-725's definition of done, driven the way a person drives it: an attività due
 * yesterday is under «In ritardo» and counted beside «Oggi» in the sidebar, and
 * completing it takes it out of both.
 *
 * The count is compared before and after rather than against a literal, because the
 * other specs of this suite leave their own attività in the same space.
 */
test.describe('Oggi', () => {
  test('un’attività di ieri è in ritardo, e Fatto la toglie dalla sezione e dal conteggio', async ({
    page,
  }) => {
    await loginAsAdmin(page)
    const me = String((await (await page.request.get('/api/auth/me')).json()).id)
    // «Yesterday» is the server's: the panel cuts on the emitter's day, which is not the
    // runner's around midnight.
    const oggi = String((await (await page.request.get('/api/activities/oggi')).json()).oggi)
    const ieri = new Date(`${oggi}T12:00:00Z`)
    ieri.setUTCDate(ieri.getUTCDate() - 1)
    const scadenza = ieri.toISOString().slice(0, 10)
    const titolo = `Sollecitare Acme ${Date.now()}`
    const created = await page.request.post('/api/activities', {
      data: { titolo, scadenza, assegnata_a: me },
    })
    expect(created.status(), await created.text()).toBe(201)

    await page.goto('/app/oggi')
    const inRitardo = page
      .getByRole('heading', { name: /In ritardo/ })
      .locator('xpath=ancestor::section')
    await expect(inRitardo.getByText(titolo)).toBeVisible()

    const badge = page.getByRole('link', { name: /^Oggi\s*\d+ in ritardo$/ }).locator('span[aria-hidden]')
    await expect(badge).toBeVisible()
    const before = Number(((await badge.textContent()) ?? '').trim())

    await inRitardo
      .getByRole('listitem')
      .filter({ hasText: titolo })
      .getByRole('button', { name: 'Fatto' })
      .click()

    await expect(inRitardo.getByText(titolo)).toHaveCount(0)
    if (before === 1) {
      await expect(badge).toHaveCount(0)
    } else {
      await expect(badge).toHaveText(String(before - 1))
    }
  })
})
