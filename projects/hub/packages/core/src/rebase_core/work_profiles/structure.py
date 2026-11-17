"""What a received work profile must not contain, and the structure it must hold (spec 2026-11-17
§ 3 steps 3 and 4).

Upstream checks structural invariants in Play New's private dashboard, so these are ours:
counts are non-negative integers, the domains' sums do not exceed the totals, there are one to ten
representative projects each with its own id, and the dates parse with the window's start not after
its end. Each function answers sentences (Italian: the CLI prints them), empty when all is well.
"""

import re
from datetime import datetime
from typing import Any

MAX_PROJECTS = 10
_MONTH = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])\Z", re.ASCII)
MAX_DEPTH = 40
MAX_COUNT = 10**9


def shape(payload: Any) -> list[str]:
    """What no honest profile has and what the database cannot store: nesting deeper than
    `MAX_DEPTH`, and a NUL character in a string or a key (JSONB refuses it). Iterative, so a
    hostile nesting costs no stack."""
    issues: list[str] = []
    pending: list[tuple[Any, int]] = [(payload, 1)]
    while pending:
        node, depth = pending.pop()
        if depth > MAX_DEPTH:
            return [f"il profilo è annidato per più di {MAX_DEPTH} livelli"]
        if isinstance(node, dict):
            if any("\x00" in key for key in node):
                issues.append("un nome di campo contiene il carattere nullo")
            pending.extend((value, depth + 1) for value in node.values())
        elif isinstance(node, list):
            pending.extend((item, depth + 1) for item in node)
        elif isinstance(node, str) and "\x00" in node:
            issues.append("un testo contiene il carattere nullo")
    return issues[:1]


def forbidden(payload: Any) -> list[str]:
    """A `repoLabel` anywhere, or a project artifact of type `file`."""
    found: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "repoLabel":
                    found.append(
                        f"{path or 'profilo'}: il nome del repository non lascia il tuo computer"
                    )
                else:
                    walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{path}[{index}]")

    walk(payload, "")
    for index, project in enumerate(payload.get("projects") or []):
        artifact = project.get("artifact") if isinstance(project, dict) else None
        if isinstance(artifact, dict) and artifact.get("type") == "file":
            found.append(
                f"projects[{index}].artifact: gli allegati non sono accettati: indica un link"
            )
    return found


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_COUNT


def _month(value: Any) -> bool:
    return isinstance(value, str) and _MONTH.match(value) is not None


def _span_issues(span: Any, where: str) -> list[str]:
    if not isinstance(span, dict):
        return []
    issues = []
    for edge in ("from", "to"):
        value = span.get(edge)
        if value is not None and not _month(value):
            issues.append(f"{where}.{edge}: «{value}» non è un mese (AAAA-MM)")
    start, end = span.get("from"), span.get("to")
    if (
        isinstance(start, str)
        and isinstance(end, str)
        and _month(start)
        and _month(end)
        and start > end
    ):
        issues.append(f"{where}: l'inizio ({start}) è dopo la fine ({end})")
    return issues


def structure(payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    volume = payload.get("volume") or {}
    projects = payload.get("projects") or []
    others = payload.get("otherProjects") or []
    domains = payload.get("domains") or []

    for key in ("products", "sessions", "instructions"):
        value = volume.get(key)
        if (key != "instructions" or value is not None) and not _is_count(value):
            issues.append(f"volume.{key}: serve un numero intero tra zero e un miliardo")
    counted: list[tuple[str, Any]] = []
    for index, project in enumerate(projects):
        counted.append((f"projects[{index}].sessions", project.get("sessions")))
        landing = project.get("landing") or {}
        counted.append((f"projects[{index}].landing.commits", landing.get("commits")))
        counted.append((f"projects[{index}].landing.reverts", landing.get("reverts")))
    for index, other in enumerate(others):
        counted.append((f"otherProjects[{index}].sessions", other.get("sessions")))
    for index, domain in enumerate(domains):
        counted.append((f"domains[{index}].products", domain.get("products")))
        counted.append((f"domains[{index}].sessions", domain.get("sessions")))
    issues.extend(
        f"{where}: serve un numero intero tra zero e un miliardo"
        for where, value in counted
        if value is not None and not _is_count(value)
    )

    if not issues and domains:
        for key in ("products", "sessions"):
            total = sum(d.get(key) or 0 for d in domains)
            if total > volume[key]:
                issues.append(
                    f"domains: {total} {key} nei campi di lavoro, più dei {volume[key]} del profilo"
                )

    if not 1 <= len(projects) <= MAX_PROJECTS:
        issues.append(
            f"projects: servono da 1 a {MAX_PROJECTS} progetti rappresentativi, ce ne sono "
            f"{len(projects)}"
        )
    ids = [project.get("id") for project in projects]
    if any(not isinstance(i, str) or not i.strip() for i in ids) or len(set(ids)) != len(ids):
        issues.append("projects: ogni progetto ha un id suo, che il testo può citare")

    window = payload.get("window") or {}
    issues.extend(_span_issues(window, "window"))
    if not _month(window.get("from")) or not _month(window.get("to")):
        issues.append("window: servono l'inizio e la fine (AAAA-MM)")
    for index, project in enumerate(projects):
        issues.extend(_span_issues(project.get("span"), f"projects[{index}].span"))
    for index, other in enumerate(others):
        issues.extend(_span_issues(other.get("span"), f"otherProjects[{index}].span"))
    generated = payload.get("generatedAt")
    if generated is not None:
        try:
            datetime.fromisoformat(str(generated))
        except ValueError:
            issues.append(f"generatedAt: «{generated}» non è una data")
    return issues
