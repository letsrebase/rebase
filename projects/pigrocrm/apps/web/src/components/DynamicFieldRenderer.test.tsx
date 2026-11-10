import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { DynamicFieldRenderer, renderFieldValue } from './DynamicFieldRenderer'
import type { FieldDefinition } from '@/lib/schema'

const field = (over: Partial<FieldDefinition>): FieldDefinition => ({
  key: 'field',
  label: 'Campo',
  type: 'text',
  required: false,
  options: [],
  ...over,
})

describe('DynamicFieldRenderer', () => {
  it('renders a text input with its label', () => {
    render(<DynamicFieldRenderer field={field({})} value={null} onChange={vi.fn()} />)
    expect(screen.getByLabelText('Campo')).toBeInTheDocument()
  })

  it('marks a required field', () => {
    render(<DynamicFieldRenderer field={field({ required: true })} value={null} onChange={vi.fn()} />)
    expect(screen.getByText('*')).toBeInTheDocument()
  })

  it('renders a textarea for textarea fields', () => {
    render(<DynamicFieldRenderer field={field({ type: 'textarea' })} value={null} onChange={vi.fn()} />)
    expect(screen.getByLabelText('Campo').tagName).toBe('TEXTAREA')
  })

  it('renders a numeric input for number fields', () => {
    render(<DynamicFieldRenderer field={field({ type: 'number' })} value={null} onChange={vi.fn()} />)
    expect(screen.getByLabelText('Campo')).toHaveAttribute('type', 'number')
  })

  it('renders a checkbox for checkbox fields', () => {
    render(<DynamicFieldRenderer field={field({ type: 'checkbox' })} value={false} onChange={vi.fn()} />)
    expect(screen.getByRole('checkbox')).toBeInTheDocument()
  })

  it('renders a date input for date fields', () => {
    render(<DynamicFieldRenderer field={field({ type: 'date' })} value={null} onChange={vi.fn()} />)
    expect(screen.getByLabelText('Campo')).toHaveAttribute('type', 'date')
  })

  it('offers every declared option for a select', async () => {
    render(
      <DynamicFieldRenderer
        field={field({ type: 'select', options: ['active', 'sospeso'] })}
        value={null}
        onChange={vi.fn()}
      />,
    )
    await userEvent.click(screen.getByRole('combobox'))
    expect(screen.getByText('active')).toBeInTheDocument()
    expect(screen.getByText('sospeso')).toBeInTheDocument()
  })

  /** A select that has been set once used to be impossible to unset: the listbox
   *  offered only the defined options and there was no other way back to empty
   *  anywhere in the UI. The backend has no such problem -- a custom field's key sent
   *  as `null` removes it (confirmed against the running API) -- so this was purely a
   *  missing control. */
  it('offers a way back to empty and reports it as null', async () => {
    const onChange = vi.fn()
    render(
      <DynamicFieldRenderer
        field={field({ type: 'select', options: ['active', 'sospeso'] })}
        value="active"
        onChange={onChange}
      />,
    )
    await userEvent.click(screen.getByRole('combobox'))
    await userEvent.click(screen.getByRole('option', { name: 'Nessuna selezione' }))
    expect(onChange).toHaveBeenCalledWith(null)
  })

  it('still reports a real option as itself, never as the clear sentinel', async () => {
    const onChange = vi.fn()
    render(
      <DynamicFieldRenderer
        field={field({ type: 'select', options: ['active', 'sospeso'] })}
        value={null}
        onChange={onChange}
      />,
    )
    await userEvent.click(screen.getByRole('combobox'))
    await userEvent.click(screen.getByRole('option', { name: 'sospeso' }))
    expect(onChange).toHaveBeenCalledWith('sospeso')
  })

  it('reports edits through onChange', async () => {
    const onChange = vi.fn()
    render(<DynamicFieldRenderer field={field({})} value={null} onChange={onChange} />)
    await userEvent.type(screen.getByLabelText('Campo'), 'x')
    expect(onChange).toHaveBeenCalledWith('x')
  })

  it('shows the server-side error next to the input', () => {
    render(
      <DynamicFieldRenderer field={field({})} value={null} onChange={vi.fn()} error="deve essere di 11 cifre" />,
    )
    expect(screen.getByText('deve essere di 11 cifre')).toBeInTheDocument()
  })
})

describe('renderFieldValue', () => {
  it('shows an em dash for an empty value', () => {
    expect(renderFieldValue(field({}), null)).toBe('—')
  })

  it('formats currency in euros', () => {
    expect(renderFieldValue(field({ type: 'currency' }), '1234.56')).toContain('1.234,56')
  })

  it('formats dates in the Italian order', () => {
    expect(renderFieldValue(field({ type: 'date' }), '2026-08-06')).toBe('06/08/2026')
  })

  it('renders booleans as Sì / No', () => {
    expect(renderFieldValue(field({ type: 'checkbox' }), true)).toBe('Sì')
    expect(renderFieldValue(field({ type: 'checkbox' }), false)).toBe('No')
  })

  /** A checkbox is the one type with no third state: a yes/no question nobody
   *  answered is a "no", and the form draws an unchecked box either way. A dash there
   *  would invent a distinction the control cannot express -- and it is what a record
   *  created before the field existed, or through the API, legitimately looks like.
   *  This is what lets the form stop writing `false` into records on edit. */
  it('renders an absent checkbox as No, never as a dash', () => {
    expect(renderFieldValue(field({ type: 'checkbox' }), undefined)).toBe('No')
    expect(renderFieldValue(field({ type: 'checkbox' }), null)).toBe('No')
  })

  it('still renders every other absent type as a dash', () => {
    expect(renderFieldValue(field({ type: 'text' }), null)).toBe('—')
    expect(renderFieldValue(field({ type: 'number' }), null)).toBe('—')
    expect(renderFieldValue(field({ type: 'currency' }), null)).toBe('—')
    expect(renderFieldValue(field({ type: 'select' }), null)).toBe('—')
    expect(renderFieldValue(field({ type: 'multiselect' }), [])).toBe('—')
  })

  it('keeps zero a value, not an absence', () => {
    expect(renderFieldValue(field({ type: 'number' }), 0)).toBe('0')
    expect(renderFieldValue(field({ type: 'currency' }), '0.00')).toContain('0,00')
  })

  it('joins multiselect values', () => {
    expect(renderFieldValue(field({ type: 'multiselect' }), ['a', 'b'])).toBe('a, b')
  })
})
