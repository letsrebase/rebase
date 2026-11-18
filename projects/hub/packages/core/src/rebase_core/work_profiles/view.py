"""A work profile rendered for an admin to read (REB-1126, spec 2026-11-17 § 4).

The admin reads the profile as upstream's `profile.md` lays it out: summary, domains with their
counts, the lenses' narratives and counts, the trajectory and the representative projects with
their URL artifacts. The payload is what the freelancer's CLI sent, checked for shape but with
unknown fields kept, so every read here is defensive: a field that is not the type the hub
expects reads as missing, never as a 500. Nothing here scores or ranks (spec non-goals): counts
are shown as they are.

`where` on a piece of prose is the key the groundedness recheck files an unsupported anchor
under (`groundedness.classify_text_fields`), so the page can set each anchor beside the text it
came from.
"""

from typing import Any

from rebase_core.models import TECH_USAGE_BANDS
from rebase_core.schema_common import ResponseModel
from rebase_core.team_schemas import CloudWorkDomain, CloudWorkProfile
from rebase_core.vocabulary import PracticeMethod, TechUsageBand
from rebase_core.work_profiles import practices as practices_module
from rebase_core.work_profiles.tech_usage import reads_true

SUMMARY_WHERE = "summary"
DOMAINS_WHERE = "domains"
PRINCIPLES_WHERE = "trajectory.principles"

_USES = (
    ("subagentDelegations", "Deleghe a sub-agenti"),
    ("taskTrackingEvents", "Eventi di tracciamento dei compiti"),
    ("builtinSlashInvocations", "Comandi slash integrati"),
)
_BUILDS = (
    ("skillsAuthored", "Skill scritte"),
    ("commandsAuthored", "Comandi scritti"),
    ("agentsAuthored", "Agenti scritti"),
    ("hooksEdited", "Hook modificati"),
    ("projectMemoryFiles", "File di memoria di progetto"),
)
_DESIGNS = (
    ("plansFirst", "Piani prima di scrivere"),
    ("subtaskTracking", "Sotto-compiti tracciati"),
    ("clarifies", "Domande di chiarimento"),
)
_INTENSITY = (
    ("observedDays", "Giorni osservati"),
    ("activeDays", "Giorni attivi"),
    ("medianSessionsPerActiveDay", "Sessioni mediane per giorno attivo"),
    ("medianSessionToolCalls", "Chiamate di strumento mediane per sessione"),
    ("longestStreak", "Serie più lunga di giorni"),
    ("peakDayToolCalls", "Chiamate di strumento nel giorno di picco"),
)


class WorkProfileCount(ResponseModel):
    label: str
    value: str


class WorkProfileLens(ResponseModel):
    """One lens of the profile: a heading, the narrative the person's own model wrote, the
    tags and the counts behind it."""

    key: str
    label: str
    narrative: str | None
    where: str
    tags: list[str]
    counts: list[WorkProfileCount]


class WorkProfileDomain(ResponseModel):
    where: str
    label: str
    products: int | None
    sessions: int | None
    note: str | None


class WorkProfileArtifact(ResponseModel):
    """A URL artifact of a project, kept as text: file artifacts are refused at the intake."""

    label: str | None
    url: str


class WorkProfileProject(ResponseModel):
    id: str
    domain: str | None
    did: str | None
    why_representative: str | None
    tech: list[str]
    types: list[str]
    sessions: int | None
    span_from: str | None
    span_to: str | None
    commits: int | None
    reverts: int | None
    artifact: WorkProfileArtifact | None


class WorkProfileShift(ResponseModel):
    metric: str
    early: str
    late: str


class WorkProfileTopic(ResponseModel):
    quarter: str
    themes: list[WorkProfileCount]


class WorkProfilePrinciple(ResponseModel):
    when: str | None
    text: str


class WorkProfileTrajectory(ResponseModel):
    narrative: str | None
    where: str
    shifts: list[WorkProfileShift]
    topics: list[WorkProfileTopic]
    principles: list[WorkProfilePrinciple]
    new_vocabulary: list[str]


class WorkProfileTechnology(ResponseModel):
    """How often and how steadily one technology shows up in the person's work (REB-1130): the
    band, which `tech_usage.band_for` derives from the months alone, with the counts it was read
    from. A label for those counts, never a score and never how well the person knows it."""

    name: str
    band: TechUsageBand
    sessions: int
    products: int
    months: int
    first_month: str
    last_month: str


class PracticeClassifier(ResponseModel):
    """The coding agent and model the person had classify the model practices (REB-1149)."""

    agent: str
    model: str | None


class WorkProfilePractice(ResponseModel):
    """What the person's sessions show of one working practice (REB-1144): in how many sessions it
    showed (`seen`) out of those that could have (`of`), and over how many products and months.
    Counts only, never how well the person works. Admins and the person only."""

    id: str
    label: str
    method: PracticeMethod
    seen: int
    of: int
    products: int
    months: int
    # Model rows only: who classified them, when the profile says so.
    classified_by: PracticeClassifier | None = None


class WorkProfileView(ResponseModel):
    window_from: str | None
    window_to: str | None
    products: int | None
    sessions: int | None
    instructions: int | None
    summary: str | None
    domains: list[WorkProfileDomain]
    lenses: list[WorkProfileLens]
    trajectory: WorkProfileTrajectory | None
    stack: list[str]
    # Strongest band first, then the more months (`technologies`). Admins and the person only:
    # `cloud_work_profile` does not read them, because the wording the person approved lists the
    # summary, the domains and the representative projects' sectors and nothing else.
    technologies: list[WorkProfileTechnology]
    # In the order of the vendored list, never sorted by the counts. Admins and the person only,
    # for the same reason as `technologies`.
    practices: list[WorkProfilePractice]
    projects: list[WorkProfileProject]
    other_projects: int


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _texts(value: Any) -> list[str]:
    return (
        [item for item in value if isinstance(item, str) and item]
        if isinstance(value, list)
        else []
    )


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == int(value):
        return int(value)
    return None


def _shown(value: Any) -> str | None:
    """A number as a count reads: whole when it is whole, else two decimals; nothing else."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _counts(source: dict[str, Any], names: tuple[tuple[str, str], ...]) -> list[WorkProfileCount]:
    found = ((label, _shown(source.get(key))) for key, label in names)
    return [WorkProfileCount(label=label, value=value) for label, value in found if value]


def _lens(key: str, label: str, source: Any, counts: list[WorkProfileCount]) -> WorkProfileLens:
    lens = _dict(source)
    return WorkProfileLens(
        key=key,
        label=label,
        narrative=_text(lens.get("narrative")),
        where=f"{key}.narrative",
        tags=_texts(lens.get("tags")),
        counts=counts,
    )


def _agentic_counts(literacy: dict[str, Any]) -> list[WorkProfileCount]:
    uses, builds, designs = (_dict(literacy.get(part)) for part in ("uses", "builds", "designs"))
    counts = _counts(uses, _USES)
    skills = _dict(uses.get("customSkillsCommands"))
    if (distinct := _shown(skills.get("distinct"))) and (
        calls := _shown(skills.get("invocations"))
    ):
        counts.append(
            WorkProfileCount(
                label="Skill e comandi propri", value=f"{distinct} (usati {calls} volte)"
            )
        )
    for key, label in (("publicMcp", "Server MCP pubblici"), ("customMcp", "Server MCP propri")):
        servers = _dict(uses.get(key))
        if (n := _shown(servers.get("servers"))) and (calls := _shown(servers.get("calls"))):
            counts.append(WorkProfileCount(label=label, value=f"{n} ({calls} chiamate)"))
    return [*counts, *_counts(builds, _BUILDS), *_counts(designs, _DESIGNS)]


def _co_thinking_counts(relationship: dict[str, Any]) -> list[WorkProfileCount]:
    counts = []
    if mode := _text(relationship.get("mode")):
        counts.append(WorkProfileCount(label="Modo", value=mode))
    for key, label in (("directing", "Dirige"), ("coThinking", "Ragiona insieme")):
        if (value := _shown(relationship.get(key))) is not None:
            counts.append(WorkProfileCount(label=label, value=f"{value}%"))
    return counts


def _intensity_counts(intensity: dict[str, Any]) -> list[WorkProfileCount]:
    counts = _counts(intensity, _INTENSITY)
    for key, label in (("cadence", "Cadenza"), ("sessionShape", "Forma delle sessioni")):
        if value := _text(intensity.get(key)):
            counts.append(WorkProfileCount(label=label, value=value))
    return counts


def _trajectory(source: Any) -> WorkProfileTrajectory | None:
    trajectory = _dict(source)
    if not trajectory:
        return None
    shifts = []
    for delta in _dicts(_dict(trajectory.get("shifts")).get("deltas")):
        metric, early, late = (
            _text(delta.get("metric")),
            _shown(delta.get("early")),
            _shown(delta.get("late")),
        )
        if metric and early is not None and late is not None:
            shifts.append(WorkProfileShift(metric=metric, early=early, late=late))
    topics = []
    for topic in _dicts(trajectory.get("topics")):
        quarter = _text(topic.get("quarter"))
        if quarter:
            themes = [
                WorkProfileCount(label=name, value=count)
                for theme in _dicts(topic.get("themes"))
                if (name := _text(theme.get("name"))) and (count := _shown(theme.get("count")))
            ]
            topics.append(WorkProfileTopic(quarter=quarter, themes=themes))
    principles = [
        WorkProfilePrinciple(when=_text(item.get("when")), text=text)
        for item in _dicts(trajectory.get("principlesAdopted"))
        if (text := _text(item.get("text")))
    ]
    return WorkProfileTrajectory(
        narrative=_text(trajectory.get("narrative")),
        where="trajectory.narrative",
        shifts=shifts,
        topics=topics,
        principles=principles,
        new_vocabulary=_texts(trajectory.get("newVocabulary")),
    )


def _project(source: dict[str, Any]) -> WorkProfileProject | None:
    project_id = _text(source.get("id"))
    if project_id is None:
        return None
    span, landing, artifact = (_dict(source.get(key)) for key in ("span", "landing", "artifact"))
    url = _text(artifact.get("url")) if artifact.get("type") != "file" else None
    return WorkProfileProject(
        id=project_id,
        domain=_text(source.get("domain")),
        did=_text(source.get("did")),
        why_representative=_text(source.get("whyRepresentative")),
        tech=_texts(source.get("tech")),
        types=_texts(source.get("type")),
        sessions=_int(source.get("sessions")),
        span_from=_text(span.get("from")),
        span_to=_text(span.get("to")),
        commits=_int(landing.get("commits")),
        reverts=_int(landing.get("reverts")),
        artifact=WorkProfileArtifact(label=_text(artifact.get("label")), url=url) if url else None,
    )


def technology_rows(rows: Any) -> list[WorkProfileTechnology]:
    """The `techUsage.technologies` rows of a stored profile that read as a technology with a
    band, strongest band first, then the more months, then the name. A row that does not,
    or that the intake's own rules would refuse (only a payload stored before a rule changed has
    one), or a name listed twice, is left out, never a 500."""
    found = []
    seen: set[str] = set()
    for row in _dicts(rows):
        name, band = _text(row.get("name")), row.get("band")
        sessions, products, months = (
            _int(row.get(key)) for key in ("sessions", "products", "months")
        )
        first, last = _text(row.get("firstMonth")), _text(row.get("lastMonth"))
        if (
            name
            and name not in seen
            and band in TECH_USAGE_BANDS
            and sessions is not None
            and products is not None
            and months is not None
            and first
            and last
            and reads_true(name, band, months, first, last)
        ):
            seen.add(name)
            found.append(
                WorkProfileTechnology(
                    name=name,
                    band=band,
                    sessions=sessions,
                    products=products,
                    months=months,
                    first_month=first,
                    last_month=last,
                )
            )
    return sorted(found, key=lambda t: (-TECH_USAGE_BANDS.index(t.band), -t.months, t.name))


def technologies(payload: dict[str, Any]) -> list[WorkProfileTechnology]:
    return technology_rows(_dict(payload.get("techUsage")).get("technologies"))


def practice_rows(rows: Any, classified_by: Any = None) -> list[WorkProfilePractice]:
    """The `practices.items` rows of a stored profile that the intake would still accept, in the
    order of the vendored list (never by the counts). A row that is no object, names a practice
    the list does not know, fails the intake's own per-item rules (only a payload stored before
    a rule changed has one) is left out, never a 500; an id listed twice keeps its first valid
    row."""
    by_id: dict[str, dict[str, Any]] = {}
    if isinstance(rows, list):
        for row in rows:
            if practices_module.reads_true(row) and row["id"] not in by_id:
                by_id[row["id"]] = row
    classifier = practices_module.read_classified_by(classified_by)
    found = []
    for ident, method in practices_module.practice_methods().items():
        row = by_id.get(ident)
        if row is None:
            continue
        found.append(
            WorkProfilePractice(
                id=ident,
                label=practices_module.PRACTICE_LABELS.get(ident, ident),
                method=method,
                seen=row["seen"],
                of=row["of"],
                products=row["products"],
                months=row["months"],
                classified_by=PracticeClassifier(**classifier)
                if classifier and method == "model"
                else None,
            )
        )
    return found


def practices(payload: dict[str, Any]) -> list[WorkProfilePractice]:
    block = _dict(payload.get("practices"))
    return practice_rows(block.get("items"), block.get("classifiedBy"))


def render_profile(payload: dict[str, Any]) -> WorkProfileView:
    """The payload of a stored profile as the admin reads it."""
    window, volume = _dict(payload.get("window")), _dict(payload.get("volume"))
    domains = [
        WorkProfileDomain(
            where=f"domains[{index}].note",
            label=label,
            products=_int(domain.get("products")),
            sessions=_int(domain.get("sessions")),
            note=_text(domain.get("note")),
        )
        for index, domain in enumerate(_dicts(payload.get("domains")))
        if (label := _text(domain.get("label")))
    ]
    literacy = _dict(payload.get("agenticLiteracy"))
    lenses = [
        _lens("cognitive", "Come ragiona", payload.get("cognitive"), []),
        _lens(
            "aiRelationship",
            "Come lavora con il modello",
            payload.get("aiRelationship"),
            _co_thinking_counts(_dict(payload.get("aiRelationship"))),
        ),
        _lens(
            "agenticLiteracy",
            "Quanto è fluente con gli agenti",
            literacy,
            _agentic_counts(literacy),
        ),
        _lens(
            "intensity",
            "Quanto è profonda la pratica",
            payload.get("intensity"),
            _intensity_counts(_dict(payload.get("intensity"))),
        ),
        _lens("distribution", "Come è distribuita", payload.get("distribution"), []),
    ]
    projects = [item for source in _dicts(payload.get("projects")) if (item := _project(source))]
    return WorkProfileView(
        window_from=_text(window.get("from")),
        window_to=_text(window.get("to")),
        products=_int(volume.get("products")),
        sessions=_int(volume.get("sessions")),
        instructions=_int(volume.get("instructions")),
        summary=_text(payload.get("summary")),
        domains=domains,
        lenses=[lens for lens in lenses if lens.narrative or lens.tags or lens.counts],
        trajectory=_trajectory(payload.get("trajectory")),
        stack=_texts(payload.get("stackAdopted")),
        technologies=technologies(payload),
        practices=practices(payload),
        projects=projects,
        other_projects=len(_dicts(payload.get("otherProjects"))),
    )


SUMMARY_CLOUD_MAX = 600


def _cut(text: str, limit: int) -> str:
    """`text` cut at a word boundary within `limit` characters, with an ellipsis."""
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"


def cloud_work_profile(
    payload: dict[str, Any], flagged: set[str] | None = None
) -> CloudWorkProfile:
    """What a company in the talent cloud reads of an approved profile (spec § 5): the summary
    (cut at `SUMMARY_CLOUD_MAX`), the domains' labels with their counts and each representative
    project's abstract domain, each once. A field named in `flagged` (the anonymity guards' hits,
    `work_profiles/guard.py`) is left out. Nothing else of the payload is read here."""
    flagged = flagged or set()
    domains = [
        CloudWorkDomain(
            label=label,
            products=_int(domain.get("products")),
            sessions=_int(domain.get("sessions")),
        )
        for index, domain in enumerate(_dicts(payload.get("domains")))
        if (label := _text(domain.get("label"))) and f"domains[{index}].label" not in flagged
    ]
    seen: dict[str, None] = {}
    for project in _dicts(payload.get("projects")):
        if (
            domain := _text(project.get("domain"))
        ) and f"{project.get('id')}.domain" not in flagged:
            seen.setdefault(domain.strip(), None)
    summary = _text(payload.get("summary")) if "summary" not in flagged else None
    return CloudWorkProfile(
        summary=_cut(summary, SUMMARY_CLOUD_MAX) if summary else None,
        domains=domains,
        project_domains=list(seen),
    )
