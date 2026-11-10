import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { WebLink } from './WebLink'

describe('WebLink', () => {
  it('opens a web address in a new tab, naming it by the address unless told otherwise', () => {
    render(<WebLink href="https://github.com/ada" />)
    const link = screen.getByRole('link', { name: 'https://github.com/ada' })
    expect(link).toHaveAttribute('href', 'https://github.com/ada')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', expect.stringContaining('noreferrer'))
  })

  it('says anything else as plain text, with no link to follow', () => {
    render(<WebLink href="javascript:alert(1)">Profilo</WebLink>)
    expect(screen.queryByRole('link')).toBeNull()
    expect(screen.getByText('Profilo')).toBeInTheDocument()
  })
})
