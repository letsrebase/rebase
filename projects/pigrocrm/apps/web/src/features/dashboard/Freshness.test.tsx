/**
 * §7.2: the age of the answer is shown, not implied. `staleTime` is 60 seconds, so the
 * figures on screen can legitimately be a minute old — and a figure with no age is a figure
 * the user believes is instantaneous.
 */
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Freshness } from './Freshness'

const NOW = new Date('2026-03-15T10:00:00Z')

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(NOW)
})
afterEach(() => vi.useRealTimers())

describe('Freshness', () => {
  it('says «adesso» for a response computed a moment ago', () => {
    render(<Freshness calculatedAt="2026-03-15T09:59:31Z" onRefresh={vi.fn()} />)
    expect(screen.getByText('Aggiornato adesso')).toBeInTheDocument()
  })

  it('uses the singular for exactly one minute', () => {
    render(<Freshness calculatedAt="2026-03-15T09:59:00Z" onRefresh={vi.fn()} />)
    expect(screen.getByText('Aggiornato 1 minuto fa')).toBeInTheDocument()
  })

  it('uses the plural beyond one', () => {
    render(<Freshness calculatedAt="2026-03-15T09:56:00Z" onRefresh={vi.fn()} />)
    expect(screen.getByText('Aggiornato 4 minuti fa')).toBeInTheDocument()
  })

  it('keeps counting as time passes rather than freezing at the first render', async () => {
    // "aggiornato 4 minuti fa" frozen at "adesso" is worse than no indicator: it makes a
    // stale figure look fresh.
    render(<Freshness calculatedAt="2026-03-15T10:00:00Z" onRefresh={vi.fn()} />)
    expect(screen.getByText('Aggiornato adesso')).toBeInTheDocument()
    await act(async () => {
      vi.advanceTimersByTime(3 * 60_000)
    })
    expect(screen.getByText('Aggiornato 3 minuti fa')).toBeInTheDocument()
  })

  it('resets the age when a refetch delivers a newer instant', async () => {
    // The state-in-an-effect version of this component got this wrong in both directions:
    // it needed a synchronous setState to notice the new prop, and it restarted the
    // interval every time one arrived.
    const { rerender } = render(
      <Freshness calculatedAt="2026-03-15T09:55:00Z" onRefresh={vi.fn()} />,
    )
    expect(screen.getByText('Aggiornato 5 minuti fa')).toBeInTheDocument()
    rerender(<Freshness calculatedAt="2026-03-15T10:00:00Z" onRefresh={vi.fn()} />)
    expect(screen.getByText('Aggiornato adesso')).toBeInTheDocument()
  })

  it('never reports a negative age when the server clock is ahead', () => {
    // `calculated_at` is the database's `transaction_timestamp()`, not the browser's
    // clock, and the two disagree. "Aggiornato -2 minuti fa" would look like a bug in the
    // figures rather than in the clocks.
    render(<Freshness calculatedAt="2026-03-15T10:02:00Z" onRefresh={vi.fn()} />)
    expect(screen.getByText('Aggiornato adesso')).toBeInTheDocument()
  })

  it('offers a way to re-read, and re-reading is all it does', () => {
    // `fireEvent`, not `userEvent`: this suite runs on fake timers, and `userEvent`'s own
    // delay between events never advances them, so the click never lands.
    const onRefresh = vi.fn()
    render(<Freshness calculatedAt="2026-03-15T10:00:00Z" onRefresh={onRefresh} />)
    fireEvent.click(screen.getByRole('button', { name: 'Ricalcola' }))
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })
})
