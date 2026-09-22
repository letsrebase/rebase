import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { AA_NON_TEXT, AA_TEXT, blendSrgb, contrastRatio, paletteFrom } from '@rebase/brand/contrast'
import { DealStageBar } from './DealStageBar'
import type { Deal, Stage } from './queries'

const STAGES: Stage[] = [
  { id: 's3', nome: 'Proposta', posizione: 3, probabilita_default: 60, tipo: 'open', code: null },
  { id: 's1', nome: 'Contatto', posizione: 1, probabilita_default: 10, tipo: 'open', code: null },
  { id: 's2', nome: 'Analisi', posizione: 2, probabilita_default: 30, tipo: 'open', code: null },
  { id: 'won', nome: 'Vinto', posizione: 4, probabilita_default: 100, tipo: 'won', code: 'won' },
  { id: 'lost', nome: 'Perso', posizione: 5, probabilita_default: 0, tipo: 'lost', code: 'lost' },
]

function deal(stageId: string): Deal {
  return { id: 'd1', nome: 'Sito', pipeline_stage_id: stageId } as unknown as Deal
}

describe('DealStageBar', () => {
  it('draws the open stages in pipeline order and marks the current one', () => {
    render(<DealStageBar deal={deal('s2')} stages={STAGES} canMove onMove={vi.fn()} />)
    const segments = screen.getAllByRole('button')
    // Ordered by `posizione`, not by the order the API happened to return them in, and
    // the two outcomes are not steps.
    expect(segments.map((button) => button.textContent)).toEqual(['Contatto', 'Analisi', 'Proposta'])
    expect(screen.getByRole('button', { name: 'Analisi' })).toHaveAttribute('aria-current', 'step')
    expect(screen.getByText(/Fase attuale/)).toHaveTextContent('Analisi')
  })

  it('moves the deal to the stage that was pressed, and never to the one it is in', async () => {
    const onMove = vi.fn()
    render(<DealStageBar deal={deal('s1')} stages={STAGES} canMove onMove={onMove} />)
    await userEvent.click(screen.getByRole('button', { name: 'Proposta' }))
    expect(onMove).toHaveBeenCalledWith('s3')
    expect(screen.getByRole('button', { name: 'Contatto' })).toBeDisabled()
  })

  it('offers nothing to press to a reader without write access', () => {
    render(<DealStageBar deal={deal('s1')} stages={STAGES} canMove={false} onMove={vi.fn()} />)
    for (const button of screen.getAllByRole('button')) expect(button).toBeDisabled()
    expect(screen.queryByText(/Clicca una fase/)).toBeNull()
  })

  it('says how a closed deal ended and how to reopen it', () => {
    render(<DealStageBar deal={deal('lost')} stages={STAGES} canMove onMove={vi.fn()} />)
    expect(screen.getByText('Perso')).toBeInTheDocument()
    expect(screen.getByText(/Per riaprirlo/)).toBeInTheDocument()
    // Every segment is pressable again: reopening is a move like any other.
    for (const button of screen.getAllByRole('button')) expect(button).toBeEnabled()
  })

  // REB-324: every pressable segment used to carry `hover:opacity-80`. Whole-element
  // opacity composites the text and its own background together, so the pair the reader
  // sees drops while the pointer is on it: the lost chip went from 4.54:1 at rest to
  // 3.73:1 hovered, the unreached one from 7.01:1 to 4.32:1. The hover now steps the
  // ground only where a solid ground can take it, and raises a border where it cannot,
  // the two treatments #232 gave the badge and the destructive button.
  describe('the hover treatment', () => {
    // jsdom loads no Tailwind, so the class list is the contract here (same pin as
    // shared/ui's badge and button tests); the measured ratios come from the palette
    // below, and the rendered proof is in the PR.
    const classesOf = (name: string) =>
      screen.getByRole('button', { name }).className.split(/\s+/)

    it('never fades the segment itself', () => {
      for (const stageId of ['s1', 'won', 'lost']) {
        const { unmount } = render(
          <DealStageBar deal={deal(stageId)} stages={STAGES} canMove onMove={vi.fn()} />,
        )
        for (const button of screen.getAllByRole('button')) {
          expect(button.className).not.toMatch(/(^|\s)hover:opacity/)
        }
        unmount()
      }
    })

    it('steps the reached chip on its own ground, the way the primary button hovers', () => {
      render(<DealStageBar deal={deal('s3')} stages={STAGES} canMove onMove={vi.fn()} />)
      // Contatto and Analisi are behind the current stage; both are reached and pressable.
      expect(classesOf('Contatto')).toContain('hover:bg-foreground/80')
      expect(classesOf('Analisi')).toContain('hover:bg-foreground/80')
    })

    it('marks the quiet chips’ hover with a border, never a deeper fill', () => {
      render(<DealStageBar deal={deal('s1')} stages={STAGES} canMove onMove={vi.fn()} />)
      const unreached = classesOf('Proposta')
      expect(unreached).toContain('bg-muted')
      expect(unreached).toContain('hover:border-muted-foreground')
      expect(unreached.filter((c) => c.startsWith('hover:bg-'))).toEqual([])
    })

    it('raises the lost chip’s own border, the destructive button’s idiom', () => {
      render(<DealStageBar deal={deal('lost')} stages={STAGES} canMove onMove={vi.fn()} />)
      const lostChip = classesOf('Contatto')
      expect(lostChip).toContain('border-destructive/50')
      expect(lostChip).toContain('hover:border-destructive')
      expect(lostChip.join(' ')).not.toMatch(/hover:bg-destructive/)
    })

    it('applies no hover to the segment the deal is already in', () => {
      // Disabled even for a writer: there is nothing to move to.
      render(<DealStageBar deal={deal('s1')} stages={STAGES} canMove onMove={vi.fn()} />)
      expect(screen.getByRole('button', { name: 'Contatto' }).className).not.toMatch(/hover:/)
    })

    it('applies no hover to a reader, who has nothing to press', () => {
      render(
        <DealStageBar deal={deal('lost')} stages={STAGES} canMove={false} onMove={vi.fn()} />,
      )
      for (const button of screen.getAllByRole('button')) {
        expect(button.className).not.toMatch(/hover:/)
      }
    })

    it('gives every hover class a token colour, never a literal', () => {
      render(<DealStageBar deal={deal('lost')} stages={STAGES} canMove onMove={vi.fn()} />)
      for (const button of screen.getAllByRole('button')) {
        for (const cls of button.className.split(/\s+/).filter((c) => c.startsWith('hover:'))) {
          expect(
            cls,
            'hover utility reads a semantic slot',
          ).toMatch(/^hover:(?:bg|border)-(?:foreground|muted-foreground|destructive)(?:\/\d+)?$/)
        }
      }
    })
  })

  // The hovered pairs, computed from the live palette rather than recorded: the same
  // reading shared/ui/tokens.test.ts does (a hand-written expected ratio would not
  // notice the day a hex moves). The grounds are the two the bar renders on inside the
  // shell: the white card the AppShell's main draws, and the Paper page behind it.
  describe('the hovered contrast', () => {
    const brandCss = readFileSync(
      fileURLToPath(import.meta.resolve('@rebase/brand/palette.css')),
      'utf-8',
    )
    const uiTokens = readFileSync(
      fileURLToPath(import.meta.resolve('@rebase/ui/tokens.css')),
      'utf-8',
    )
    const palette = paletteFrom(brandCss)

    function slotHex(name: string): string {
      const raw = uiTokens.match(new RegExp(`(?:^|\\s)--${name}:\\s*([^;]+);`, 'm'))?.[1]?.trim()
      if (!raw) throw new Error(`--${name} is not declared in @rebase/ui/tokens.css`)
      const ref = raw.match(/^var\((--[\w-]+)\)$/)?.[1]
      const hex = raw.startsWith('#') ? raw : palette[ref ?? '']
      if (!hex) throw new Error(`${name} resolves to ${raw}, which is not a palette colour`)
      return hex
    }

    const destructive = slotHex('destructive')
    const mutedForeground = slotHex('muted-foreground')
    const foreground = slotHex('foreground')
    const background = slotHex('background')
    const muted = slotHex('muted')

    for (const groundName of ['card', 'background'] as const) {
      const ground = slotHex(groundName)

      // The two border-hovers do not touch the fill, so the hovered text pair is the
      // resting one: what must hold is that the resting pair clears AA, which the fade
      // used to break (3.73:1 and 4.32:1 hovered against these rests of 4.54:1+).
      it(`keeps the lost chip’s text at AA under the border hover on ${groundName}`, () => {
        const tint = blendSrgb(destructive, ground, 10)
        expect(contrastRatio(destructive, tint)).toBeGreaterThanOrEqual(AA_TEXT)
        // The hover's only cue is the border: it must clear the non-text bar against
        // the page beside the chip, which is why #232 took the full slot.
        expect(contrastRatio(destructive, ground)).toBeGreaterThanOrEqual(AA_NON_TEXT)
      })

      it(`keeps the unreached chip’s text at AA under the border hover on ${groundName}`, () => {
        expect(contrastRatio(mutedForeground, muted)).toBeGreaterThanOrEqual(AA_TEXT)
        expect(contrastRatio(mutedForeground, ground)).toBeGreaterThanOrEqual(AA_NON_TEXT)
      })

      // The filled chip does change ground on hover: text-background over the ink at
      // 80% composited on whatever the bar sits on.
      it(`keeps the reached chip’s text at AA on its hover ground over ${groundName}`, () => {
        const fill = blendSrgb(foreground, ground, 80)
        expect(contrastRatio(background, fill)).toBeGreaterThanOrEqual(AA_TEXT)
      })
    }

    it('pins why the fade was the bug: the same pairs at 80% opacity fail AA', () => {
      // Whole-element opacity composites both the text and its own fill toward the
      // page beneath, which is the mechanism REB-324 retires. These are the pairs the
      // pointer used to see on Paper (the card's 3.73:1 and 4.32:1); asserted as
      // failures so nobody reintroduces the fade as an accessibility-neutral flourish.
      const page = slotHex('background')
      const fade = (color: string) => blendSrgb(color, page, 80)
      expect(
        contrastRatio(fade(destructive), fade(blendSrgb(destructive, page, 10))),
      ).toBeLessThan(AA_TEXT)
      expect(contrastRatio(fade(mutedForeground), fade(muted))).toBeLessThan(AA_TEXT)
    })
  })
})
