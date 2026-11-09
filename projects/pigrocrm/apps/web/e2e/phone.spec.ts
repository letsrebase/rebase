import { expect, test } from '@playwright/test'
import { ensureSpaceHasWork, loginAsAdmin } from './helpers'

/**
 * REB-959's definition of done, driven the way a person drives it on a phone: the app opens
 * on the bottom bar, an activity is added from «+» without leaving the page and is marked
 * done from «Attività»; and the desktop keeps its sidebar and has no bar.
 */
const PHONE = { width: 390, height: 844 }

test.describe('the phone shell', () => {
  test('an activity is added from «+» and marked done from Attività', async ({ page }) => {
    await page.setViewportSize(PHONE)
    await loginAsAdmin(page)
    await ensureSpaceHasWork(page)
    await page.goto('/app')

    const bar = page.getByRole('navigation', { name: 'Navigazione principale' })
    await expect(bar.getByRole('link', { name: 'Home' })).toBeVisible()
    await expect(bar.getByRole('link', { name: /^Attività/ })).toBeVisible()
    await expect(bar.getByRole('button', { name: 'Aggiungi' })).toBeVisible()
    await expect(bar.getByRole('button', { name: 'Altro' })).toBeVisible()
    // No sidebar to toggle: the bar is the navigation.
    await expect(page.getByRole('button', { name: 'Espandi il menu' })).toHaveCount(0)

    const title = `Chiamare da telefono ${Date.now()}`
    await bar.getByRole('button', { name: 'Aggiungi' }).click()
    await page.getByRole('button', { name: /^Attività\s*Una cosa/ }).click()
    const today = String((await (await page.request.get('/api/activities/today')).json()).today)
    await page.getByLabel('Titolo').fill(title)
    await page.getByLabel('Scadenza').fill(today)
    await page.getByRole('dialog').getByRole('button', { name: 'Aggiungi', exact: true }).click()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    // Added without navigating away from where the person was.
    await expect(page).toHaveURL(/\/app\/?(\?|$)/)

    await bar.getByRole('link', { name: /^Attività/ }).click()
    await expect(page).toHaveURL(/\/app\/activities/)
    const row = page.getByRole('listitem').filter({ hasText: title })
    await expect(row).toBeVisible()
    await row.getByRole('button', { name: 'Fatto' }).click()
    await expect(page.getByRole('listitem').filter({ hasText: title })).toHaveCount(0)
  })

  test('«Altro» lists the places the bar does not draw', async ({ page }) => {
    await page.setViewportSize(PHONE)
    await loginAsAdmin(page)
    await page.goto('/app')
    await page.getByRole('button', { name: 'Altro' }).click()
    const sheet = page.getByRole('dialog', { name: 'Altro' })
    for (const name of ['Calendario', 'Clienti', 'Persone', 'Fatture', 'Agenti', 'Impostazioni']) {
      await expect(sheet.getByRole('link', { name })).toBeVisible()
    }
  })

  test('the desktop keeps its sidebar and draws no bar', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 })
    await loginAsAdmin(page)
    await page.goto('/app')
    await expect(page.getByRole('button', { name: 'Comprimi il menu' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Aggiungi' })).toHaveCount(0)
  })
})
