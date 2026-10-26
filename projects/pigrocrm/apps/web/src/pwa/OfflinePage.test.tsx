import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { OfflinePage } from './OfflinePage'

describe('OfflinePage', () => {
  it('says it is offline and shows no data of anyone', () => {
    render(<OfflinePage onRetry={() => {}} />)
    expect(screen.getByRole('heading', { level: 1, name: 'Sei offline' })).toBeInTheDocument()
    expect(screen.getByRole('main')).toBeInTheDocument()
  })

  it('asks the network again on "Riprova"', async () => {
    const onRetry = vi.fn()
    render(<OfflinePage onRetry={onRetry} />)
    await userEvent.click(screen.getByRole('button', { name: 'Riprova' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })
})
