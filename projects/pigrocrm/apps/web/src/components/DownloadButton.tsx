import { useState, type ComponentProps } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import { toProblem } from '@/lib/api'
import { downloadFromApi } from '@/lib/download'

/** A button that downloads an API file (see `downloadFromApi`) and says so when it is
 *  refused, where a plain link could only fail as a browser error page. */
export function DownloadButton({
  path,
  children,
  ...props
}: { path: string } & Omit<ComponentProps<typeof Button>, 'onClick' | 'asChild'>) {
  const [busy, setBusy] = useState(false)
  return (
    <Button
      {...props}
      disabled={busy || props.disabled}
      onClick={() => {
        setBusy(true)
        downloadFromApi(path)
          .catch((error: unknown) => toast.error(toProblem(error).detail))
          .finally(() => setBusy(false))
      }}
    >
      {children}
    </Button>
  )
}
