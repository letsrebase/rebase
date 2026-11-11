import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Label } from '@rebase/ui/label'
import { useMunicipalities, type LegalEntityRecord } from './queries'

/** What the form holds for the three fields the place is read from. */
export interface AddressDraft {
  city: string
  province: string
  country: string
}

/** `LegalEntityRecord['place']` without the `readonly` the generated type carries. */
type Place = LegalEntityRecord['place']

/**
 * Where the legal entity's address is, as the server read it from ISTAT's list of municipalities
 * (REB-1069), under the city and province fields.
 *
 * The answer belongs to the saved row, so it is shown only while the three fields still read
 * as saved: once the person edits the city, the province or the country, the previous answer
 * describes an address that is no longer on screen and the next save brings a new one. A city
 * that matches no municipality of its province is still saved; this is where the person fixes it,
 * by picking the municipality of the province, whose name the form then writes in the city field.
 */
export function MunicipalityPlace({
  place,
  saved,
  draft,
  disabled,
  onPick,
}: {
  place: Place
  saved: AddressDraft
  draft: AddressDraft
  disabled: boolean
  onPick: (name: string) => void
}) {
  const unchanged =
    draft.city === saved.city && draft.province === saved.province && draft.country === saved.country
  if (!unchanged || place.status === 'not_italian') return null
  if (place.status === 'matched') {
    return (
      <p className="text-muted-foreground text-sm" data-testid="place-matched">
        Comune riconosciuto: {place.municipality_name}, {place.region_name} (codice ISTAT{' '}
        {place.municipality_code}).
      </p>
    )
  }
  // Nothing to flag on an address with no city yet: the field is simply empty.
  if (draft.city.trim() === '') return null
  return <UnknownMunicipality province={draft.province} disabled={disabled} onPick={onPick} />
}

function UnknownMunicipality({
  province,
  disabled,
  onPick,
}: {
  province: string
  disabled: boolean
  onPick: (name: string) => void
}) {
  const options = useMunicipalities(province)
  const code = province.trim().toUpperCase()
  let body
  if (code.length !== 2) {
    body = 'Indica la provincia con la sigla di due lettere per scegliere il comune dall’elenco.'
  } else if (options.isError) {
    body = 'Elenco dei comuni non disponibile: riprova tra poco.'
  } else if (options.data && options.data.length === 0) {
    body = `La sigla ${code} non è nell’elenco ISTAT: controlla la provincia.`
  } else {
    body = null
  }
  return (
    <div className="space-y-2" data-testid="place-unknown">
      <p className="text-sm" role="status">
        Comune non riconosciuto: nell’elenco ISTAT non c’è un comune con questo nome nella
        provincia. Il salvataggio funziona lo stesso; per le imposte comunali serve però il comune
        esatto.
      </p>
      {body ? (
        <p className="text-muted-foreground text-sm">{body}</p>
      ) : (
        <div className="max-w-sm space-y-2">
          <Label htmlFor="municipality-picker">Comune dell’elenco ISTAT</Label>
          <Select
            // Never holds a value: choosing writes the name into the city field, which is the
            // one the save sends, and this control goes away with the next save's answer.
            value=""
            disabled={disabled || !options.data}
            onValueChange={(codeChosen) => {
              const chosen = options.data?.find((o) => o.municipality_code === codeChosen)
              if (chosen) onPick(chosen.name)
            }}
          >
            <SelectTrigger id="municipality-picker" className="w-full">
              <SelectValue placeholder={options.isLoading ? 'Carico…' : `Scegli il comune (${code})`} />
            </SelectTrigger>
            <SelectContent>
              {options.data?.map((option) => (
                <SelectItem key={option.municipality_code} value={option.municipality_code}>
                  {option.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
    </div>
  )
}
