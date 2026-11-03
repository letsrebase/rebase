"""The anonymous CV's shape (spec § The shape, REB-862).

The model's native structured output, so it follows the card's strict style (`card_draft`):
every field required, nullable by a union, `extra="forbid"`. The limits are far above a long
two-page CV; they only stop a runaway answer from being stored. `AnonymousCvDraft` adds the
private `identity`, which is the guard's input and nothing else: it is never stored, logged,
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
    """A bullet; `title` is the bold lead of a CV that groups engagements under one role."""

    title: Short | None
    text: Line


class Experience(_Strict):
    role: Short
    company: Short | None
    location: Short | None
    period: Short | None
    points: list[Point] = Field(max_length=30)


class Group(_Strict):
    area: Short
    entries: list[Entry] = Field(max_length=80)


class Title(_Strict):
    title: Short
    institution: Short | None
    location: Short | None
    period: Short | None


class Language(_Strict):
    language: Short
    level: Short | None


class Section(_Strict):
    title: Short
    entries: list[Line] = Field(max_length=60)


class AnonymousCv(_Strict):
    """The CV as it is printed. `language` only picks the printed headings; the text is the
    CV's own."""

    language: Literal["it", "en"]
    title: Short
    summary: Annotated[SafeStr, Field(max_length=4000)]
    experience: list[Experience] = Field(max_length=60)
    skills: list[Group] = Field(max_length=80)
    education: list[Title] = Field(max_length=40)
    certifications: list[Entry] = Field(max_length=60)
    languages: list[Language] = Field(max_length=30)
    other_sections: list[Section] = Field(max_length=30)


class Identity(_Strict):
    """What the CV says of the person, each only as the CV states it: what the guard looks
    for in `AnonymousCv`. Never kept. Every number the CV gives, since a CV often has a
    mobile and an office line."""

    first_name: Short | None
    last_name: Short | None
    email: Short | None
    phones: list[Short] = Field(max_length=10)
    link: list[Contact] = Field(max_length=20)


class AnonymousCvDraft(_Strict):
    """The model's whole answer: the CV, then the private identity the guard reads."""

    cv: AnonymousCv
    identity: Identity


def strings(cv: AnonymousCv) -> list[tuple[str, str]]:
    """Every string of `cv` as `(path, value)`, in document order, a missing optional left
    out. The path is what the guard answers and what a log may carry: never the value."""
    out: list[tuple[str, str]] = [("title", cv.title), ("summary", cv.summary)]

    def add(path: str, value: str | None) -> None:
        if value is not None:
            out.append((path, value))

    for i, role in enumerate(cv.experience):
        base = f"experience[{i}]"
        add(f"{base}.role", role.role)
        add(f"{base}.company", role.company)
        add(f"{base}.location", role.location)
        add(f"{base}.period", role.period)
        for j, point in enumerate(role.points):
            add(f"{base}.points[{j}].title", point.title)
            add(f"{base}.points[{j}].text", point.text)
    for i, group in enumerate(cv.skills):
        add(f"skills[{i}].area", group.area)
        for j, entry in enumerate(group.entries):
            add(f"skills[{i}].entries[{j}]", entry)
    for i, title in enumerate(cv.education):
        add(f"education[{i}].title", title.title)
        add(f"education[{i}].institution", title.institution)
        add(f"education[{i}].location", title.location)
        add(f"education[{i}].period", title.period)
    for i, cert in enumerate(cv.certifications):
        add(f"certifications[{i}]", cert)
    for i, language in enumerate(cv.languages):
        add(f"languages[{i}].language", language.language)
        add(f"languages[{i}].level", language.level)
    for i, section in enumerate(cv.other_sections):
        add(f"other_sections[{i}].title", section.title)
        for j, entry in enumerate(section.entries):
            add(f"other_sections[{i}].entries[{j}]", entry)
    return out
