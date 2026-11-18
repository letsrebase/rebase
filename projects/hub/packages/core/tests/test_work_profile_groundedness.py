"""The groundedness port against upstream's own test vectors (`test/groundedness.test.mjs`,
`domains.test.mjs`, `orchestration-followups.test.mjs` of letsrebase/rebase-verify). A change
upstream that moves a score shows up here when the fork merges it, and the vendored lexicon must
equal the fork's generated artifact."""

import copy
import json
from pathlib import Path
from typing import Any

from rebase_core.work_profiles.groundedness import (
    assess_groundedness,
    extract_anchors,
    split_tech,
    tech_lexicon,
)

SAMPLE = Path(__file__).parent / "fixtures" / "work_profile_candidate.sample.json"


def base_profile() -> dict[str, Any]:
    return {
        "schema": "playnew-profile/v1",
        "contact": {"name": "X", "email": "x@y.io", "city": "Milano", "status": "employed"},
        "window": {"from": "2026-01", "to": "2026-05"},
        "volume": {"products": 30, "sessions": 236, "instructions": 3800},
        "summary": None,
        "cognitive": {"tags": ["research-first"], "narrative": None},
        "projects": [
            {
                "id": "p1",
                "type": ["product-build"],
                "domain": "Creator intelligence platform.",
                "span": {"from": "2026-02", "to": "2026-05"},
                "sessions": 59,
                "did": None,
                "whyRepresentative": None,
                "tech": ["Inngest", "Supabase/Postgres", "Playwright (E2E)"],
                "landing": {"commits": 153, "reverts": 0, "revertChurn": "low", "checksRun": True},
                "artifact": None,
            }
        ],
        "otherProjects": [],
        "trajectory": None,
        "stackAdopted": ["Inngest", "Supabase/Postgres"],
        "authenticity": {"score": 100, "manifestHash": "abc"},
    }


def anomalies_of(profile: dict[str, Any], **match: str) -> list[dict[str, str]]:
    found = assess_groundedness(profile).anomalies
    return [a for a in found if all(a[k] == v for k, v in match.items())]


def test_prose_anchored_by_numbers_tech_and_tags_in_the_data_scores_100() -> None:
    p = base_profile()
    p["summary"] = (
        "Across 236 sessions on 30 products, the work blends product-build with research-first "
        "verification, on Inngest and Supabase."
    )
    p["projects"][0]["did"] = "153 commits, 0 reverts. Worked on Supabase/Postgres and Playwright."
    g = assess_groundedness(p)
    assert g.score == 100, g.anomalies
    assert g.anomalies == []


def test_unsupported_numbers_flag_the_field_that_contains_them() -> None:
    p = base_profile()
    p["summary"] = "Delivered 999 commits across the window."
    g = assess_groundedness(p)
    assert g.score is None or g.score < 100  # JavaScript: null < 100
    assert {"where": "summary", "anchor": "999", "kind": "number"} in g.anomalies


def test_ai_tooling_vocabulary_is_pre_grounded() -> None:
    p = base_profile()
    p["cognitive"]["narrative"] = (
        "Uses Claude as the main daily tool, delegates to sub-agents via the SDK and an MCP server."
    )
    assert all(a["kind"] != "ai-tooling" for a in assess_groundedness(p).anomalies)


def test_a_technology_that_is_not_in_the_data_is_flagged() -> None:
    p = base_profile()
    p["cognitive"]["narrative"] = "Built everything on Drizzle and Stripe."
    assert anomalies_of(p, where="cognitive.narrative", kind="tech")


def test_rounding_tolerance_a_5_percent_rounded_number_still_matches() -> None:
    p = base_profile()
    p["summary"] = "About 150 commits over the period."
    assert anomalies_of(p, kind="number") == []


def test_the_score_is_none_when_there_are_too_few_anchors_to_judge() -> None:
    assert assess_groundedness(base_profile()).score is None


def test_a_detector_vocabulary_technology_is_verified_not_invisible() -> None:
    absent = base_profile()
    absent["cognitive"]["narrative"] = "The data layer runs on Firebase."
    assert anomalies_of(absent, where="cognitive.narrative", kind="tech")

    present = base_profile()
    present["projects"][0]["tech"].append("Firebase/Firestore")
    present["stackAdopted"].append("Firebase/Firestore")
    present["cognitive"]["narrative"] = "The data layer runs on Firebase."
    assert not anomalies_of(present, where="cognitive.narrative", kind="tech")


def test_orchestration_counts_of_0_and_1_are_not_pooled() -> None:
    p = base_profile()
    p["projects"][0]["metrics"] = {
        "researchToMutation": None,
        "delegation": 0,
        "orchestration": {
            "delegation": 0,
            "tools": {"claude-code": 59},
            "toolCount": 1,
            "toolOverlap": None,
            "dispatchCommands": 0,
        },
    }
    p["summary"] = "Landed 100% of migrations without incident across 236 sessions."
    assert anomalies_of(p, where="summary", anchor="100%")


def test_a_count_of_1_in_the_data_does_not_ground_a_fabricated_100_percent() -> None:
    p = base_profile()
    p["projects"][0]["metrics"] = {
        "researchToMutation": None,
        "delegation": 1,
        "orchestration": None,
    }
    p["summary"] = "Landed 100% of migrations without incident across 236 sessions."
    assert anomalies_of(p, where="summary", anchor="100%")


def test_an_intensity_median_of_1_does_not_ground_a_fabricated_100_percent() -> None:
    p = base_profile()
    p["intensity"] = {
        "observedDays": 30,
        "activeDays": 12,
        "activeDaysRatio": 0.4,
        "longestStreak": 3,
        "medianSessionsPerActiveDay": 1,
        "medianSessionToolCalls": 25,
        "peakDayToolCalls": 90,
    }
    p["summary"] = "Shipped 100% of the planned migrations across 236 sessions."
    assert anomalies_of(p, where="summary", anchor="100%")


def test_honest_share_citations_still_ground() -> None:
    p = base_profile()
    p["intensity"] = {
        "observedDays": 100,
        "activeDays": 87,
        "activeDaysRatio": 0.87,
        "longestStreak": 9,
        "medianSessionsPerActiveDay": 3,
        "medianSessionToolCalls": 40,
        "peakDayToolCalls": 200,
        "narrative": "Active on 87% of observed days.",
    }
    p["distribution"] = {
        "products": 3,
        "sessions": 236,
        "meanSessionsPerProduct": 79,
        "medianSessionsPerProduct": 80,
        "top3Share": 1.0,
        "multiMonthProducts": 2,
        "multiMonthShare": 0.67,
        "shape": "deep focus",
        "narrative": "The top 3 products account for 100% of all sessions.",
    }
    assert anomalies_of(p, kind="percent") == []


def test_percent_format_trajectory_shifts_ground_their_percent_citations() -> None:
    p = base_profile()
    p["trajectory"] = {
        "shifts": {
            "deltas": [
                {
                    "metric": "delegation rate",
                    "format": "percent",
                    "early": 0.12,
                    "late": 0.3,
                    "dir": "up",
                }
            ]
        },
        "narrative": "Delegation rate moved from 12% to 30% across the window.",
    }
    assert anomalies_of(p, kind="percent") == []


def _domains_profile(domains: list[dict[str, Any]]) -> dict[str, Any]:
    p = base_profile()
    p["volume"] = {"products": 3, "sessions": 10, "instructions": 100}
    p["domains"] = domains
    return p


def test_domain_counts_above_the_deterministic_totals_are_flagged() -> None:
    inflated = _domains_profile([{"label": "everything", "products": 5, "sessions": 9}])
    assert any(
        a["where"] == "domains" and "5 products" in a["anchor"]
        for a in assess_groundedness(inflated).anomalies
    )
    honest = _domains_profile([{"label": "everything", "products": 3, "sessions": 10}])
    assert not [a for a in assess_groundedness(honest).anomalies if a["where"] == "domains"]


def _ground_profile() -> dict[str, Any]:
    p = base_profile()
    p["summary"] = (
        "Across 236 sessions on 30 products, the work blends product-build with research-first "
        "verification."
    )
    p["projects"][0]["sessions"] = 50
    p["projects"][0]["did"] = "Fanned out across 2 CLIs: 38 sessions via Claude, 12 via opencode."
    p["projects"][0]["metrics"] = {
        "researchToMutation": 2.1,
        "delegation": 4,
        "orchestration": {
            "delegation": 4,
            "tools": {"claude-code": 38, "opencode": 12},
            "toolCount": 2,
            "toolOverlap": True,
            "dispatchCommands": 0,
        },
    }
    return p


def test_per_cli_orchestration_counts_are_grounded() -> None:
    g = assess_groundedness(_ground_profile())
    assert [a for a in g.anomalies if a["kind"] == "number"] == []
    assert g.score == 100


def test_the_tools_pool_stays_specific() -> None:
    p = _ground_profile()
    p["projects"][0]["did"] = (
        "Fanned out across 2 CLIs: 38 via Claude, 12 via opencode, plus 777 phantom runs."
    )
    assert anomalies_of(p, kind="number", anchor="777")


def test_the_upstream_sample_scores_what_the_cli_claimed_for_it() -> None:
    """The fork's own fixture carries the score its Node implementation computed: 89, 25 of 28."""
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    g = assess_groundedness(sample)
    assert (g.score, g.supported, g.total) == (89, 25, 28)
    assert {(a["where"], a["anchor"], a["kind"]) for a in g.anomalies} == {
        ("summary", "research-first", "tag"),
        ("trajectory.narrative", "sdk", "tech"),
        ("p1.did", "sql", "tech"),
    }


def test_a_half_rounds_up_like_javascript() -> None:
    """Math.round(0.5) is 1 and Math.round(2.5) is 3; Python's round gives 0 and 2."""
    p = base_profile()
    # 1 supported anchor of 8 -> 12.5 -> 13.
    p["summary"] = "999 998 997 996 995 994 993 236"
    g = assess_groundedness(p)
    assert (g.supported, g.total, g.score) == (1, 8, 13)


def test_the_anchors_of_a_chunk_of_prose() -> None:
    kinds = {(a.kind, a.raw) for a in extract_anchors("12 commits, 40% done in 2026-03 on Zod.")}
    assert {("number", "12"), ("number", "40"), ("percent", "40%"), ("period", "2026-03")} <= kinds
    assert ("tech", "zod") in {(a.kind, a.raw) for a in extract_anchors("Zod")}
    assert split_tech("Supabase/Postgres") == ["supabase", "postgres"]


def test_the_vendored_lexicon_is_the_forks_artifact_and_sorted() -> None:
    tokens = list(tech_lexicon())
    assert tokens == sorted(tokens) and len(tokens) == len(set(tokens))
    assert {"firebase", "zod", "supabase"} <= set(tokens)


def test_a_copy_of_the_profile_is_not_changed() -> None:
    p = base_profile()
    before = copy.deepcopy(p)
    assess_groundedness(p)
    assert p == before
