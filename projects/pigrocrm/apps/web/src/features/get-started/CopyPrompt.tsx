/**
 * A ready prompt for the assistant, readable and selectable on the page, with a button
 * that copies it (ORB-182). `CopyPrompt` collapses it behind a native `<details>`: the
 * line above stays short, and the prompt is one click away with no state to keep.
 * `PromptBody` is the prompt alone, for a caller that opens and closes it with a control
 * of its own (the start page's step rows, REB-222).
 */
import { Copy } from 'lucide-react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'

export function CopyPrompt({ text, summary = 'Prompt per l’assistente' }: { text: string; summary?: string }) {
  return (
    <details className="mt-2 text-sm">
      <summary className="text-muted-foreground cursor-pointer select-none underline-offset-4 hover:underline">
        {summary}
      </summary>
      <PromptBody text={text} className="mt-2" />
    </details>
  )
}

export function PromptBody({ text, id, className }: { text: string; id?: string; className?: string }) {
  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      toast.success('Prompt copiato')
    } catch {
      // A browser that refuses the clipboard: the text is on the page, select it.
      toast.error('Non riesco a copiare: seleziona il testo qui sotto')
    }
  }
  return (
    <div id={id} className={cn('space-y-2 text-sm', className)}>
      <p className="bg-muted/50 whitespace-pre-wrap border p-3 leading-relaxed">{text}</p>
      <Button type="button" variant="outline" size="sm" onClick={() => void copy()}>
        <Copy className="mr-2 size-4" aria-hidden />
        Copia il prompt
      </Button>
    </div>
  )
}
