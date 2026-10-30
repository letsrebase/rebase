"""The anonymous CV's shape (spec § The shape, REB-862).

The model's native structured output, so it follows the card's strict style (`card_draft`):
every field required, nullable by a union, `extra="forbid"`. The limits are far above a long
two-page CV; they only stop a runaway answer from being stored. `AnonymousCvDraft` adds the
private `identita`, which is the guard's input and nothing else: it is never stored, logged,
returned or printed.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from rebase_core.validation import SafeStr

Line = Annotated[SafeStr, Field(max_length=2000)]
Short = Annotated[SafeStr, Field(max_length=300)]
Entry = Annotated[SafeStr, Field(max_length=300)]
Contact = Annotated[SafeStr, Field(max_length=600)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Punto(_Strict):
    """A bullet; `titolo` is the bold lead of a CV that groups engagements under one role."""

    titolo: Short | None
    testo: Line


class Esperienza(_Strict):
    ruolo: Short
    azienda: Short | None
    luogo: Short | None
    periodo: Short | None
    punti: list[Punto] = Field(max_length=30)


class Gruppo(_Strict):
    area: Short
    voci: list[Entry] = Field(max_length=80)


class Titolo(_Strict):
    titolo: Short
    istituto: Short | None
    luogo: Short | None
    periodo: Short | None


class Lingua(_Strict):
    lingua: Short
    livello: Short | None


class Sezione(_Strict):
    titolo: Short
    voci: list[Line] = Field(max_length=60)


class AnonymousCv(_Strict):
    """The CV as it is printed. `lingua` only picks the printed headings; the text is the
    CV's own."""

    lingua: Literal["it", "en"]
    titolo: Short
    sommario: Annotated[SafeStr, Field(max_length=4000)]
    esperienze: list[Esperienza] = Field(max_length=60)
    competenze: list[Gruppo] = Field(max_length=80)
    formazione: list[Titolo] = Field(max_length=40)
    certificazioni: list[Entry] = Field(max_length=60)
    lingue: list[Lingua] = Field(max_length=30)
    altre_sezioni: list[Sezione] = Field(max_length=30)


class Identita(_Strict):
    """What the CV says of the person, each only as the CV states it: what the guard looks
    for in `AnonymousCv`. Never kept."""

    nome: Short | None
    cognome: Short | None
    email: Short | None
    telefono: Short | None
    link: list[Contact] = Field(max_length=20)


class AnonymousCvDraft(_Strict):
    """The model's whole answer: the CV, then the private identity the guard reads."""

    cv: AnonymousCv
    identita: Identita


def strings(cv: AnonymousCv) -> list[tuple[str, str]]:
    """Every string of `cv` as `(path, value)`, in document order, a missing optional left
    out. The path is what the guard answers and what a log may carry: never the value."""
    out: list[tuple[str, str]] = [("titolo", cv.titolo), ("sommario", cv.sommario)]

    def add(path: str, value: str | None) -> None:
        if value is not None:
            out.append((path, value))

    for i, role in enumerate(cv.esperienze):
        base = f"esperienze[{i}]"
        add(f"{base}.ruolo", role.ruolo)
        add(f"{base}.azienda", role.azienda)
        add(f"{base}.luogo", role.luogo)
        add(f"{base}.periodo", role.periodo)
        for j, punto in enumerate(role.punti):
            add(f"{base}.punti[{j}].titolo", punto.titolo)
            add(f"{base}.punti[{j}].testo", punto.testo)
    for i, group in enumerate(cv.competenze):
        add(f"competenze[{i}].area", group.area)
        for j, voce in enumerate(group.voci):
            add(f"competenze[{i}].voci[{j}]", voce)
    for i, title in enumerate(cv.formazione):
        add(f"formazione[{i}].titolo", title.titolo)
        add(f"formazione[{i}].istituto", title.istituto)
        add(f"formazione[{i}].luogo", title.luogo)
        add(f"formazione[{i}].periodo", title.periodo)
    for i, cert in enumerate(cv.certificazioni):
        add(f"certificazioni[{i}]", cert)
    for i, language in enumerate(cv.lingue):
        add(f"lingue[{i}].lingua", language.lingua)
        add(f"lingue[{i}].livello", language.livello)
    for i, section in enumerate(cv.altre_sezioni):
        add(f"altre_sezioni[{i}].titolo", section.titolo)
        for j, voce in enumerate(section.voci):
            add(f"altre_sezioni[{i}].voci[{j}]", voce)
    return out
