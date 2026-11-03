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


class Point(_Strict):
    """A bullet; `titolo` is the bold lead of a CV that groups engagements under one role."""

    title: Short | None
    text: Line


class Experience(_Strict):
    role: Short
    company: Short | None
    location: Short | None
    period: Short | None
    punti: list[Point] = Field(max_length=30)


class Group(_Strict):
    area: Short
    entries: list[Entry] = Field(max_length=80)


class Title(_Strict):
    title: Short
    istituto: Short | None
    location: Short | None
    period: Short | None


class Language(_Strict):
    language: Short
    level: Short | None


class Section(_Strict):
    title: Short
    entries: list[Line] = Field(max_length=60)


class AnonymousCv(_Strict):
    """The CV as it is printed. `lingua` only picks the printed headings; the text is the
    CV's own."""

    language: Literal["it", "en"]
    title: Short
    sommario: Annotated[SafeStr, Field(max_length=4000)]
    esperienze: list[Experience] = Field(max_length=60)
    skills: list[Group] = Field(max_length=80)
    formazione: list[Title] = Field(max_length=40)
    certificazioni: list[Entry] = Field(max_length=60)
    languages: list[Language] = Field(max_length=30)
    other_sections: list[Section] = Field(max_length=30)


class Identity(_Strict):
    """What the CV says of the person, each only as the CV states it: what the guard looks
    for in `AnonymousCv`. Never kept. Every number the CV gives, since a CV often has a
    mobile and an office line."""

    nome: Short | None
    last_name: Short | None
    email: Short | None
    telefoni: list[Short] = Field(max_length=10)
    link: list[Contact] = Field(max_length=20)


class AnonymousCvDraft(_Strict):
    """The model's whole answer: the CV, then the private identity the guard reads."""

    cv: AnonymousCv
    identity: Identity


def strings(cv: AnonymousCv) -> list[tuple[str, str]]:
    """Every string of `cv` as `(path, value)`, in document order, a missing optional left
    out. The path is what the guard answers and what a log may carry: never the value."""
    out: list[tuple[str, str]] = [("titolo", cv.title), ("sommario", cv.sommario)]

    def add(path: str, value: str | None) -> None:
        if value is not None:
            out.append((path, value))

    for i, role in enumerate(cv.esperienze):
        base = f"esperienze[{i}]"
        add(f"{base}.ruolo", role.role)
        add(f"{base}.azienda", role.company)
        add(f"{base}.luogo", role.location)
        add(f"{base}.periodo", role.period)
        for j, point in enumerate(role.punti):
            add(f"{base}.punti[{j}].titolo", point.title)
            add(f"{base}.punti[{j}].testo", point.text)
    for i, group in enumerate(cv.skills):
        add(f"competenze[{i}].area", group.area)
        for j, entry in enumerate(group.entries):
            add(f"competenze[{i}].voci[{j}]", entry)
    for i, title in enumerate(cv.formazione):
        add(f"formazione[{i}].titolo", title.title)
        add(f"formazione[{i}].istituto", title.istituto)
        add(f"formazione[{i}].luogo", title.location)
        add(f"formazione[{i}].periodo", title.period)
    for i, cert in enumerate(cv.certificazioni):
        add(f"certificazioni[{i}]", cert)
    for i, language in enumerate(cv.languages):
        add(f"lingue[{i}].lingua", language.language)
        add(f"lingue[{i}].livello", language.level)
    for i, section in enumerate(cv.other_sections):
        add(f"altre_sezioni[{i}].titolo", section.title)
        for j, entry in enumerate(section.entries):
            add(f"altre_sezioni[{i}].voci[{j}]", entry)
    return out
