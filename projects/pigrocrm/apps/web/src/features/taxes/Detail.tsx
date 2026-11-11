import type { ReactNode } from 'react'
import { Button } from '@rebase/ui/button'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@rebase/ui/sheet'

/** The panel beside a table from `lg`, a full-screen sheet below it. */
export function Detail({
  inline,
  label,
  onClose,
  children,
}: {
  inline: boolean
  label: string
  onClose: () => void
  children: ReactNode
}) {
  if (inline) {
    return (
      <aside aria-label={label} className="flex w-82.5 shrink-0 flex-col self-start border bg-card">
        <div className="flex justify-end border-b px-2 py-1">
          <Button variant="ghost" size="sm" onClick={onClose}>
            Chiudi
          </Button>
        </div>
        {children}
      </aside>
    )
  }
  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent
        side="right"
        closeLabel="Chiudi"
        className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-none"
      >
        <SheetTitle className="sr-only">{label}</SheetTitle>
        <SheetDescription className="sr-only">{label}</SheetDescription>
        <div className="flex min-h-0 flex-1 flex-col pt-12">{children}</div>
      </SheetContent>
    </Sheet>
  )
}
