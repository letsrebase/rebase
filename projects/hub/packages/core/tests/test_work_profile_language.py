"""A work profile names its language and the page shows its enum codes in Italian."""

from work_profile_helpers import sample

from rebase_core.work_profiles.view import render_profile


def _counts(profile: dict, key: str) -> dict[str, str]:
    view = render_profile(profile)
    lens = next(lens for lens in view.lenses if lens.key == key)
    return {count.label: count.value for count in lens.counts}


def test_the_enum_codes_of_an_italian_profile_show_as_italian_labels() -> None:
    profile = sample() | {
        "language": "it",
        "intensity": {"cadence": "daily driver", "sessionShape": "deep sessions"},
        "aiRelationship": {"mode": "co-thinking-leaning"},
        "distribution": {"shape": "deep focus"},
        "trajectory": {
            "shifts": {"deltas": [{"metric": "delegation rate", "early": 1, "late": 2}]},
            "topics": [{"quarter": "2026-Q1", "themes": [{"name": "dev tooling", "count": 3}]}],
        },
    }
    view = render_profile(profile)

    assert view.language == "it"
    intensity = _counts(profile, "intensity")
    assert intensity["Cadenza"] == "uso quotidiano"
    assert intensity["Forma delle sessioni"] == "sessioni profonde"
    assert _counts(profile, "aiRelationship")["Modo"] == "tende a ragionare insieme"
    assert _counts(profile, "distribution")["Forma"] == "focus profondo"
    assert view.trajectory is not None
    assert view.trajectory.shifts[0].metric == "Tasso di delega"
    assert view.trajectory.topics[0].themes[0].label == "strumenti di sviluppo"


def test_an_unknown_code_shows_as_itself_and_a_profile_without_language_reads_as_english() -> None:
    profile = sample() | {"intensity": {"cadence": "fortnightly"}}
    profile.pop("language", None)

    assert render_profile(profile).language == "en"
    assert _counts(profile, "intensity")["Cadenza"] == "fortnightly"
