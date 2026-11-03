import type { ComponentProps } from 'react'
import { cn } from '@rebase/ui/cn'
import { isHttpUrl } from '@/lib/url'

/** An external link that only exists for an `http(s)` address: anything else is the
 *  text alone (the address itself unless `children` say otherwise), never an anchor. */
export function WebLink({ href, className, children, ...rest }: ComponentProps<'a'> & { href: string }) {
  if (!isHttpUrl(href)) return <>{children ?? href}</>
  return (
    <a
      {...rest}
      href={href}
      target="_blank"
      rel="noreferrer"
      className={cn('underline underline-offset-2', className)}
    >
      {children ?? href}
    </a>
  )
}
