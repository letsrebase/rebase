import { expect, test } from '@playwright/test'
import { loginAsAdmin } from './helpers'

/**
 * The onboarding of REB-957. `e2e-setup.sh` answers «Decido dopo» for the seeded space (the
 * screen would otherwise stand in front of every other spec), so what is exercised here is
 * the way back: «Primi passi» lists the questions first, the three steps run outside the
 * shell, and the space lands on its Home with the profile stored. The seeded fiscal profile
 * means the regime step reads «già impostato» and writes nothing.
 *
 * The space keeps its profile afterwards (`company`, every module on, so no other spec
 * notices). On a stack that is kept alive between runs the question is already answered
 * and the test says so instead of failing.
 */
test('a space that skipped the onboarding answers it from «Primi passi»', async ({ page }) => {
  await loginAsAdmin(page)
  const modules = (await (await page.request.get('/api/settings/modules')).json()) as {
    onboarding: string
  }
  test.skip(modules.onboarding !== 'skipped', 'this stack already answered the onboarding')

  await page.goto('/app/get-started')
  const first = page.getByTestId('get-started').getByRole('listitem').first()
  await expect(first).toContainText('Come lavora il tuo spazio')
  await first.getByRole('link', { name: /Le tre domande/ }).click()

  await expect(page.getByTestId('onboarding')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Come lavori?' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Clienti' })).toHaveCount(0)

  await page.getByRole('radio', { name: /Come azienda/ }).click()
  await page.getByRole('button', { name: 'Avanti' }).click()
  await expect(page.getByText(/già impostato/)).toBeVisible()
  await page.getByRole('button', { name: 'Avanti' }).click()

  await expect(page.getByRole('heading', { name: 'Ecco come sarà il tuo spazio' })).toBeVisible()
  await page.getByRole('button', { name: 'Conferma' }).click()

  await expect(page).toHaveURL(/\/app\/?(\?|$)/)
  await expect(page.getByTestId('onboarding')).toHaveCount(0)
  const after = (await (await page.request.get('/api/settings/modules')).json()) as {
    profile: string
    onboarding: string
  }
  expect(after).toMatchObject({ profile: 'company', onboarding: 'done' })
})
