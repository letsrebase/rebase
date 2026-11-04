"""Contact points as a fixture writes them.

A person or a customer built by hand (`Person(first_name=..., contact_points=points(email=...))`)
is born with its points through the relationship's cascade; one built through a service takes
`points_in(...)` as its `contact_points`. Each value given becomes one primary `work` point, in
the order email, phone, LinkedIn, normalised by the same function the service uses.
"""

from pigrocrm.core.contact_points.models import ContactPoint
from pigrocrm.core.contact_points.schemas import ContactPointIn
from pigrocrm.core.contact_points.service import prepare


def points_in(
    *, email: str | None = None, phone: str | None = None, linkedin: str | None = None
) -> list[ContactPointIn]:
    given = (("email", email), ("phone", phone), ("linkedin", linkedin))
    return [ContactPointIn(kind=kind, value=value) for kind, value in given if value]  # type: ignore[arg-type]


def points(
    *, email: str | None = None, phone: str | None = None, linkedin: str | None = None
) -> list[ContactPoint]:
    return [
        ContactPoint(
            kind=point.kind,
            value=point.value,
            normalized=point.normalized,
            label=point.label,
            note=point.note,
            is_primary=point.is_primary,
            position=position,
        )
        for position, point in enumerate(
            prepare("person", points_in(email=email, phone=phone, linkedin=linkedin))
        )
    ]
