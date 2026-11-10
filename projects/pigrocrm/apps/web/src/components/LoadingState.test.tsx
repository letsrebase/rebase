import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { LoadingState } from './LoadingState'

describe('LoadingState', () => {
  it('announces the wait as a status and draws the brand loader beside it', () => {
    const { container } = render(<LoadingState label="Caricamento timeline…" />)
    expect(screen.getByRole('status', { name: 'Caricamento timeline…' })).toBeInTheDocument()
    expect(container.querySelector('[data-slot="loader"]')).not.toBeNull()
  })

  it('says «Caricamento…» when the place has no sentence of its own', () => {
    render(<LoadingState />)
    expect(screen.getByRole('status', { name: 'Caricamento…' })).toBeInTheDocument()
  })
})
