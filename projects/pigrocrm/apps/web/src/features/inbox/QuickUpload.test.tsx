import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchWithRefresh } from '@/lib/api'
import { toast } from '@rebase/ui/sonner'
import { QuickUpload } from './QuickUpload'
import { item, renderWithQuery } from './testing'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, fetchWithRefresh: vi.fn() }
})

const navigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigate }))
vi.mock('@rebase/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function created(count: number) {
  return new Response(JSON.stringify({ items: Array.from({ length: count }, () => item()) }), { status: 201 })
}

// The inputs sit outside the sheet, which Radix marks `pointer-events: none` while it is open: a
// person never clicks them (the buttons do, by `.click()`), so the check is off for the upload only.
const picker = userEvent.setup({ pointerEventsCheck: 0 })

function open() {
  const onOpenChange = vi.fn()
  renderWithQuery(<QuickUpload open onOpenChange={onOpenChange} />)
  return onOpenChange
}

beforeEach(() => {
  vi.mocked(fetchWithRefresh).mockReset()
  vi.mocked(toast.success).mockReset()
  vi.mocked(toast.error).mockReset()
  navigate.mockReset()
})

describe('«Carica un documento» from the phone’s «+»', () => {
  it('offers the camera and a file, the camera asking for the back one', () => {
    open()
    expect(screen.getByRole('button', { name: 'Scatta una foto' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Scegli un file' })).toBeInTheDocument()
    const camera = screen.getByLabelText('Scatta una foto', { selector: 'input' })
    expect(camera).toHaveAttribute('capture', 'environment')
    expect(camera).toHaveAttribute('accept', 'image/*')
  })

  it('adds a photo to the inbox without leaving the page, and says so with «Apri»', async () => {
    vi.mocked(fetchWithRefresh).mockImplementation(async () => created(1))
    const onOpenChange = open()
    const photo = new File(['x'], 'scontrino.jpg', { type: 'image/jpeg' })
    await picker.upload(screen.getByLabelText('Scatta una foto', { selector: 'input' }), photo)

    await waitFor(() => expect(toast.success).toHaveBeenCalled())
    expect(onOpenChange).toHaveBeenCalledWith(false)
    expect(vi.mocked(fetchWithRefresh).mock.calls[0]?.[0]).toBe('/api/inbox/upload')
    expect(navigate).not.toHaveBeenCalled()
    const [message, options] = vi.mocked(toast.success).mock.calls[0] ?? []
    expect(message).toBe('1 documento caricato in In arrivo.')
    expect(options).toMatchObject({ action: { label: 'Apri' } })
    const action = (options as unknown as { action: { onClick: () => void } }).action
    action.onClick()
    expect(navigate).toHaveBeenCalledWith({ to: '/app/inbox', search: { tab: 'pending' } })
  })

  it('takes several files, a ZIP among them, and counts the documents and not the archive', async () => {
    vi.mocked(fetchWithRefresh)
      .mockImplementationOnce(async () => created(1))
      .mockImplementationOnce(
        async () =>
          new Response(JSON.stringify({ items: [item({ kind: 'archive', state: 'filed' }), item(), item()] }), { status: 201 }),
      )
    open()
    await picker.upload(screen.getByLabelText('Scegli un file', { selector: 'input' }), [
      new File(['x'], 'a.pdf', { type: 'application/pdf' }),
      new File(['x'], 'lotto.zip', { type: 'application/zip' }),
    ])
    await waitFor(() => expect(toast.success).toHaveBeenCalled())
    expect(toast.success).toHaveBeenCalledWith('3 documenti caricati in In arrivo.', expect.anything())
  })

  it('says why a refused file was refused, in a toast of its own', async () => {
    vi.mocked(fetchWithRefresh).mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Formato non accettato.', code: 'validation_failed', title: 'x', status: 422, instance: '/' }), { status: 422 }),
    )
    open()
    await picker.upload(screen.getByLabelText('Scegli un file', { selector: 'input' }), new File(['x'], 'strano.pdf', { type: 'application/pdf' }))
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('strano.pdf: Non caricato: formato non accettato.'))
    expect(toast.success).not.toHaveBeenCalled()
  })
})
