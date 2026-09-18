/**
 * A ready prompt for the assistant, readable and selectable on the page, with a button
 * that copies it (ORB-182). Collapsed behind a native `<details>`: the step's own line
 * stays short, and the prompt is one click away with no state to keep.
 */
import { Copy } from 'lucide-react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'

export function CopyPrompt({ text, summary = 'Prompt per l’assistente' }: { text: string; summary?: string }) {
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
    <details className="mt-2 text-sm">
      <summary className="text-muted-foreground cursor-pointer select-none underline-offset-4 hover:underline">
        {summary}
      </summary>
      <div className="mt-2 space-y-2">
        <p className="bg-muted/50 whitespace-pre-wrap rounded-md border p-3 leading-relaxed">{text}</p>
        <Button type="button" variant="outline" size="sm" onClick={() => void copy()}>
          <Copy className="mr-2 size-4" aria-hidden />
          Copia il prompt
        </Button>
      </div>
    </details>
  )
}
