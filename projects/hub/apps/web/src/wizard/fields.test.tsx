import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { render } from '@testing-library/react'
import { compile } from 'tailwindcss'
import { describe, expect, it } from 'vitest'
import { ChoiceField, FileField, LinksField, LongTextField, TextField } from './fields'

/**
 * The fields are drawn on two kinds of page: the public wizards, inside the `.site`
 * scope, and the signed-in edit pages, outside it. A custom property declared only
 * inside `.site` resolves to nothing on the second kind, and the field it paints
 * silently loses its fill, its text colour or its line (REB-611: «Da remoto» chosen and
 * nothing drawn). jsdom has no cascade, so this compiles the real stylesheet for the
 * classes the components actually render and reads which custom properties the result
 * reaches for.
 */

const styles = resolve(dirname(fileURLToPath(import.meta.url)), '../styles')
const tokens = resolve(styles, 'tokens.css')

/** `@import`s the way the app's bundler resolves them. The two animation packages only
 *  add `animate-*` utilities, none of which is a colour, a line or a focus ring. */
async function loadStylesheet(id: string, base: string) {
  if (id === 'tw-animate-css' || id === 'shadcn/tailwind.css') return { path: id, base, content: '' }
  const path = id.startsWith('.')
    ? resolve(base, id)
    : fileURLToPath(import.meta.resolve(id === 'tailwindcss' ? 'tailwindcss/index.css' : id))
  return { path, base: dirname(path), content: readFileSync(path, 'utf-8') }
}

/** The `{ ... }` blocks of `css` whose header (the text before the brace) matches, at any
 *  depth, as `[start, end)` offsets that include the header's braces. */
function blocks(css: string, header: RegExp): [number, number][] {
  const found: [number, number][] = []
  for (let open = css.indexOf('{'); open !== -1; open = css.indexOf('{', open + 1)) {
    const start = Math.max(css.lastIndexOf('{', open - 1), css.lastIndexOf('}', open), css.lastIndexOf(';', open)) + 1
    if (!header.test(css.slice(start, open).trim())) continue
    let depth = 0
    let end = open
    do {
      if (css[end] === '{') depth++
      else if (css[end] === '}') depth--
      end++
    } while (depth > 0 && end < css.length)
    found.push([open, end])
  }
  return found
}

describe('the custom properties the wizard fields draw with', () => {
  it('are all declared outside .site, so the signed-in edit pages draw the same fields', async () => {
    const { container } = render(
      <>
        <ChoiceField
          value="ibrido"
          onChange={() => {}}
          options={[
            { value: 'remoto', label: 'Da remoto', hint: 'Nessun giorno fisso in sede.' },
            { value: 'ibrido', label: 'Ibrido', hint: 'Qualche giorno in sede.' },
          ]}
        />
        <FileField value={null} onChange={() => {}} accept=".pdf" hint="PDF" />
        <TextField value="" onChange={() => {}} aria-label="Nome" />
        <LongTextField value="" onChange={() => {}} aria-label="Progetto" />
        <LinksField value={[]} onChange={() => {}} />
      </>,
    )
    const candidates = new Set(
      [...container.querySelectorAll('[class]')].flatMap((element) => element.className.toString().split(/\s+/)),
    )

    const compiler = await compile(readFileSync(tokens, 'utf-8'), { base: styles, loadStylesheet })
    const sheet = compiler.build([...candidates])

    // Declared for everyone: every custom property some rule sets outside `.site`.
    let outside = sheet
    for (const [open, end] of blocks(sheet, /(^|[\s,>+~])\.site\b/).reverse()) {
      outside = outside.slice(0, open) + outside.slice(end)
    }
    const declared = new Set([...outside.matchAll(/(--[\w-]+)\s*:/g)].map((match) => match[1]!))

    // Read by these components: what the utilities the candidates produced reach for, not
    // the rest of the sheet, so a `var(--x)` elsewhere is not this test's business.
    const read = new Set<string>()
    for (const [open, end] of blocks(sheet, /^@layer utilities$/)) {
      for (const match of sheet.slice(open, end).matchAll(/var\((--[\w-]+)/g)) {
        if (!match[1]!.startsWith('--tw-')) read.add(match[1]!)
      }
    }

    expect(read.size).toBeGreaterThan(0)
    expect([...read].filter((name) => !declared.has(name))).toEqual([])
  })
})
