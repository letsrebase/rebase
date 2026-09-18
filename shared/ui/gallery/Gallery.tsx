import type { ReactNode } from 'react'
import { toast } from 'sonner'

import { Badge } from '../badge'
import { Button } from '../button'
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '../card'
import { Checkbox } from '../checkbox'
import { cn } from '../cn'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '../dialog'
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from '../dropdown-menu'
import { Input } from '../input'
import { Label } from '../label'
import {
  Popover,
  PopoverContent,
  PopoverDescription,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
} from '../popover'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from '../select'
import { Separator } from '../separator'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '../sheet'
import { Skeleton } from '../skeleton'
import { Toaster } from '../sonner'
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from '../table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../tabs'
import { Textarea } from '../textarea'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '../tooltip'

import './gallery.css'

/**
 * Every primitive the package ships, in every variant, size and state, on one page.
 * It exists so a visual check has a single URL instead of a tour of two products:
 * `pnpm --filter @rebase/ui dev` serves it, `build` emits it, and `uishot`/`uislop`
 * run against it.
 *
 * Overlays cannot be shown by rendering them, since each one lives behind a trigger
 * and a portal. `?open=dialog|sheet|menu|popover|select` opens exactly one on load, so
 * a screenshot of an open surface is a URL rather than a sequence of clicks.
 */
const open = new URLSearchParams(window.location.search).get('open')

const BUTTON_VARIANTS = ['default', 'outline', 'secondary', 'ghost', 'destructive', 'link'] as const
const BUTTON_SIZES = ['xs', 'sm', 'default', 'lg'] as const
const BADGE_VARIANTS = ['default', 'secondary', 'destructive', 'outline', 'ghost', 'link', 'pill'] as const
const DOT_TINTS = ['ink', 'muted', 'accent', 'gold', 'danger'] as const

function Section({
  title,
  note,
  children,
  className,
}: {
  title: string
  note?: string
  children: ReactNode
  className?: string
}) {
  return (
    <section className="flex flex-col gap-3 border-t border-border py-8" id={title.toLowerCase().replace(/\s+/g, '-')}>
      <header className="flex flex-col gap-1">
        <h2 className="font-heading text-base font-medium">{title}</h2>
        {note ? <p className="max-w-prose text-sm text-muted-foreground">{note}</p> : null}
      </header>
      <div className={cn('flex flex-wrap items-start gap-6', className)}>{children}</div>
    </section>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-48 flex-col gap-2">
      <span className="text-xs tracking-wide text-muted-foreground">{label}</span>
      <div className="flex flex-wrap items-center gap-2">{children}</div>
    </div>
  )
}

export function Gallery() {
  return (
    <TooltipProvider>
      <div className="mx-auto flex max-w-5xl flex-col px-6 py-10">
        <header className="flex flex-col gap-2 pb-6">
          <h1 className="font-heading text-2xl font-medium">@rebase/ui</h1>
          <p className="max-w-prose text-sm text-muted-foreground">
            The eighteen primitives the hub and the CRM share, on the tokens of the
            application-variant record: squared, 1px ink lines, one 4px step shadow on
            what floats and none on what rests.
          </p>
        </header>

        <Section
          title="Button"
          note="Six variants, four sizes, plus the icon sizes, disabled and invalid. The primary carries the 2px line of the site; the rest carry 1px."
        >
          {BUTTON_VARIANTS.map((variant) => (
            <Row key={variant} label={variant}>
              <Button variant={variant}>Salva</Button>
              <Button variant={variant} disabled>
                Salva
              </Button>
            </Row>
          ))}
          <Row label="sizes">
            {BUTTON_SIZES.map((size) => (
              <Button key={size} size={size}>
                {size}
              </Button>
            ))}
          </Row>
          <Row label="icon">
            <Button size="icon" aria-label="Aggiungi">
              +
            </Button>
            <Button size="icon-sm" variant="outline" aria-label="Aggiungi">
              +
            </Button>
          </Row>
          <Row label="invalid">
            <Button aria-invalid>Salva</Button>
            <Button variant="outline" aria-invalid>
              Salva
            </Button>
          </Row>
        </Section>

        <Section title="Badge" note="Seven variants and the five dot tints.">
          {BADGE_VARIANTS.map((variant) => (
            <Row key={variant} label={variant}>
              <Badge variant={variant}>Attiva</Badge>
            </Row>
          ))}
          <Row label="dots">
            {DOT_TINTS.map((dot) => (
              <Badge key={dot} variant="pill" dot={dot}>
                {dot}
              </Badge>
            ))}
          </Row>
        </Section>

        <Section
          title="Fields"
          note="Input, textarea, checkbox and label, each in its resting, filled, disabled and invalid state."
        >
          <Row label="input">
            <div className="flex w-56 flex-col gap-2">
              <Label htmlFor="g-input">Ragione sociale</Label>
              <Input id="g-input" placeholder="Studio Marangoni S.r.l." />
              <Input defaultValue="Studio Marangoni S.r.l." />
              <Input placeholder="Disabilitato" disabled />
              <Input defaultValue="non valido" aria-invalid />
            </div>
          </Row>
          <Row label="textarea">
            <div className="flex w-56 flex-col gap-2">
              <Textarea placeholder="Note" />
              <Textarea defaultValue="non valido" aria-invalid />
              <Textarea placeholder="Disabilitato" disabled />
            </div>
          </Row>
          <Row label="checkbox">
            <div className="flex flex-col gap-2">
              <span className="flex items-center gap-2">
                <Checkbox id="g-cb-1" />
                <Label htmlFor="g-cb-1">Non selezionata</Label>
              </span>
              <span className="flex items-center gap-2">
                <Checkbox id="g-cb-2" defaultChecked />
                <Label htmlFor="g-cb-2">Selezionata</Label>
              </span>
              <span className="flex items-center gap-2">
                <Checkbox id="g-cb-3" disabled />
                <Label htmlFor="g-cb-3">Disabilitata</Label>
              </span>
              <span className="flex items-center gap-2">
                <Checkbox id="g-cb-4" aria-invalid />
                <Label htmlFor="g-cb-4">Non valida</Label>
              </span>
            </div>
          </Row>
          <Row label="select">
            <Select defaultOpen={open === 'select'}>
              <SelectTrigger className="w-48">
                <SelectValue placeholder="Scegli uno stato" />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  <SelectLabel>Stato</SelectLabel>
                  <SelectItem value="bozza">Bozza</SelectItem>
                  <SelectItem value="inviata">Inviata</SelectItem>
                  <SelectSeparator />
                  <SelectItem value="pagata">Pagata</SelectItem>
                </SelectGroup>
              </SelectContent>
            </Select>
            <Select disabled>
              <SelectTrigger className="w-48" size="sm">
                <SelectValue placeholder="Disabilitato" />
              </SelectTrigger>
              <SelectContent />
            </Select>
          </Row>
        </Section>

        <Section title="Card" note="The surface that sits in the page: a line, no shadow." className="items-stretch">
          <Card className="w-72">
            <CardHeader>
              <CardTitle>Studio Marangoni S.r.l.</CardTitle>
              <CardDescription>Cliente dal 2024</CardDescription>
              <CardAction>
                <Button variant="ghost" size="icon-sm" aria-label="Azioni">
                  &#8943;
                </Button>
              </CardAction>
            </CardHeader>
            <CardContent>
              <p className="text-sm">Tre fatture aperte, 4.200 euro.</p>
            </CardContent>
            <CardFooter>
              <Button variant="outline">Apri</Button>
            </CardFooter>
          </Card>
          <Card className="w-72">
            <CardHeader>
              <CardTitle>Skeleton</CardTitle>
              <CardDescription>Lo stato di caricamento</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-2">
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-8 w-24" />
            </CardContent>
          </Card>
        </Section>

        <Section title="Table" note="56px rows, a 40px header, the ink separator and the Paper hover." className="items-stretch">
          <div className="w-full border border-border bg-card px-3">
            <Table>
              <TableCaption>Le ultime fatture</TableCaption>
              <TableHeader>
                <TableRow>
                  <TableHead>Numero</TableHead>
                  <TableHead>Cliente</TableHead>
                  <TableHead>Stato</TableHead>
                  <TableHead className="text-right">Importo</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow>
                  <TableCell>2026/014</TableCell>
                  <TableCell>Studio Marangoni S.r.l.</TableCell>
                  <TableCell>
                    <Badge variant="pill" dot="accent">
                      Inviata
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right">1.200,00</TableCell>
                </TableRow>
                <TableRow data-state="selected">
                  <TableCell>2026/013</TableCell>
                  <TableCell>Nuvola Digitale S.r.l.</TableCell>
                  <TableCell>
                    <Badge variant="pill" dot="ink">
                      Pagata
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right">840,00</TableCell>
                </TableRow>
              </TableBody>
              <TableFooter>
                <TableRow>
                  <TableCell colSpan={3}>Totale</TableCell>
                  <TableCell className="text-right">2.040,00</TableCell>
                </TableRow>
              </TableFooter>
            </Table>
          </div>
        </Section>

        <Section title="Tabs" note="Both variants: the underlined row, and the same row with no rule under it.">
          <Tabs defaultValue="uno" className="w-72">
            <TabsList>
              <TabsTrigger value="uno">Dati</TabsTrigger>
              <TabsTrigger value="due">Fatture</TabsTrigger>
              <TabsTrigger value="tre" disabled>
                Documenti
              </TabsTrigger>
            </TabsList>
            <TabsContent value="uno">Il contenuto della prima scheda.</TabsContent>
            <TabsContent value="due">Il contenuto della seconda.</TabsContent>
          </Tabs>
          <Tabs defaultValue="uno" className="w-72">
            <TabsList variant="line">
              <TabsTrigger value="uno">Dati</TabsTrigger>
              <TabsTrigger value="due">Fatture</TabsTrigger>
            </TabsList>
            <TabsContent value="uno">Senza la riga sotto.</TabsContent>
          </Tabs>
        </Section>

        <Section title="Separator" note="Both orientations.">
          <div className="flex w-56 flex-col gap-2">
            <span className="text-sm">Sopra</span>
            <Separator />
            <span className="text-sm">Sotto</span>
          </div>
          <div className="flex h-12 items-center gap-2">
            <span className="text-sm">Sinistra</span>
            <Separator orientation="vertical" />
            <span className="text-sm">Destra</span>
          </div>
        </Section>

        <Section
          title="Floating surfaces"
          note="Dialog, sheet, dropdown, popover and tooltip: each on the 4px step shadow. Open one on load with ?open=dialog, sheet, menu, popover or select."
        >
          <Row label="dialog">
            <Dialog defaultOpen={open === 'dialog'}>
              <DialogTrigger asChild>
                <Button variant="outline">Apri il dialog</Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Eliminare il cliente?</DialogTitle>
                  <DialogDescription>
                    Le fatture restano, il cliente no. L&apos;operazione non si annulla.
                  </DialogDescription>
                </DialogHeader>
                <DialogFooter>
                  <DialogClose asChild>
                    <Button variant="outline">Annulla</Button>
                  </DialogClose>
                  <Button variant="destructive">Elimina</Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </Row>
          <Row label="sheet">
            <Sheet defaultOpen={open === 'sheet'}>
              <SheetTrigger asChild>
                <Button variant="outline">Apri lo sheet</Button>
              </SheetTrigger>
              <SheetContent>
                <SheetHeader>
                  <SheetTitle>Nuova fattura</SheetTitle>
                  <SheetDescription>I campi minimi, il resto dopo.</SheetDescription>
                </SheetHeader>
                <div className="flex flex-col gap-3 px-4">
                  <Label htmlFor="g-sheet-amount">Importo</Label>
                  <Input id="g-sheet-amount" placeholder="1.200,00" />
                </div>
                <SheetFooter>
                  <Button>Salva</Button>
                </SheetFooter>
              </SheetContent>
            </Sheet>
          </Row>
          <Row label="dropdown">
            <DropdownMenu defaultOpen={open === 'menu'}>
              <DropdownMenuTrigger asChild>
                <Button variant="outline">Azioni</Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                <DropdownMenuLabel>Fattura</DropdownMenuLabel>
                <DropdownMenuItem>
                  Duplica
                  <DropdownMenuShortcut>D</DropdownMenuShortcut>
                </DropdownMenuItem>
                <DropdownMenuCheckboxItem checked>Segna come pagata</DropdownMenuCheckboxItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem variant="destructive">Elimina</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </Row>
          <Row label="popover">
            <Popover defaultOpen={open === 'popover'}>
              <PopoverTrigger asChild>
                <Button variant="outline">Dettagli</Button>
              </PopoverTrigger>
              <PopoverContent>
                <PopoverHeader>
                  <PopoverTitle>Pagamento</PopoverTitle>
                  <PopoverDescription>Bonifico, 30 giorni data fattura.</PopoverDescription>
                </PopoverHeader>
              </PopoverContent>
            </Popover>
          </Row>
          <Row label="tooltip">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="outline">Passaci sopra</Button>
              </TooltipTrigger>
              <TooltipContent>Il totale include l&apos;IVA</TooltipContent>
            </Tooltip>
          </Row>
          <Row label="toast">
            <Button variant="outline" onClick={() => toast.success('Fattura salvata')}>
              Mostra un toast
            </Button>
          </Row>
        </Section>
        <Toaster />
      </div>
    </TooltipProvider>
  )
}
