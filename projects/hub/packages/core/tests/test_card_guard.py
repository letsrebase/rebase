"""The anonymity guard (REB-838): what in a card names the person, read against the
identifiers the sources add to the person's own name. No database, no model."""

from typing import Any

import pytest
from fakes_cards import CARD

from rebase_core.card_guard import KnownIdentifiers, identifying
from rebase_core.team_schemas import Card

KNOWN = KnownIdentifiers(
    last_name="Lovelace",
    first_name="Ada",
    github_login="ada-codes",
    repo_names=["my-lib", "billing_engine", "app", "api"],
    hostnames=["mario.dev", "www.ada-writes.example"],
    employers=["Initech S.r.l.", "Globex"],
)


def _card(**fields: Any) -> Card:
    return Card.model_validate({**CARD, **fields})


def test_an_ordinary_card_is_clean() -> None:
    assert identifying(_card(), KNOWN) == []


def test_github_login_in_summary_fails() -> None:
    assert identifying(_card(profile_summary="Mantiene ada-codes e altri progetti."), KNOWN) == [
        "profile_summary"
    ]
    # Without the punctuation, in any case.
    assert identifying(_card(profile_summary="Su GitHub come ADA CODES."), KNOWN) == [
        "profile_summary"
    ]


def test_repo_name_in_summary_fails() -> None:
    assert identifying(_card(profile_summary="Autore di my-lib, una libreria."), KNOWN) == [
        "profile_summary"
    ]
    assert identifying(_card(industries=["fintech", "billing engine"]), KNOWN) == ["industries"]


@pytest.mark.parametrize("written", ["my-lib", "my_lib", "my lib", "My Lib", "MY-LIB", "MyLib"])
def test_repo_name_variants_match(written: str) -> None:
    assert identifying(_card(profile_summary=f"Ha scritto {written} da zero."), KNOWN) == [
        "profile_summary"
    ]


def test_repo_name_inside_a_longer_word_is_not_a_match() -> None:
    assert (
        identifying(_card(profile_summary="Una my-library di utilità, e mylibraries."), KNOWN) == []
    )


@pytest.mark.parametrize("written", ["mario.dev", "https://mario.dev/blog"])
def test_hostname_in_any_field_fails(written: str) -> None:
    # `https://` also trips the address rule, as it always did: the field is named once.
    assert identifying(_card(location=written), KNOWN) == ["location"]
    assert identifying(_card(industries=["fintech", written]), KNOWN) == ["industries"]


def test_hostname_written_as_words_fails_and_www_is_dropped() -> None:
    assert identifying(_card(profile_summary="Scrive su mario dev da anni."), KNOWN) == [
        "profile_summary"
    ]
    assert identifying(_card(profile_summary="Il blog ada writes example."), KNOWN) == [
        "profile_summary"
    ]


def test_employer_in_role_fails() -> None:
    assert identifying(_card(role="Backend developer in Globex"), KNOWN) == ["role"]


def test_employer_is_read_without_its_legal_form() -> None:
    assert identifying(_card(profile_summary="Nove anni in Initech, API Python."), KNOWN) == [
        "profile_summary"
    ]


@pytest.mark.parametrize("name", ["app", "api", "ab", "web", "App Api"])
def test_generic_repo_name_app_does_not_fail(name: str) -> None:
    known = KnownIdentifiers(last_name="Lovelace", repo_names=[name])
    card = _card(profile_summary="Sviluppa un'app e una api per il web.", skills=["Python", "api"])
    assert identifying(card, known) == []


def test_a_repo_named_like_one_of_the_cards_skills_is_the_technology() -> None:
    known = KnownIdentifiers(last_name="Lovelace", repo_names=["fastapi"])
    assert identifying(_card(profile_summary="API in FastAPI e AWS."), known) == []
    # The same name with no such skill is a repository the card must not name.
    assert identifying(_card(skills=["Python"], profile_summary="API in FastAPI."), known) == [
        "profile_summary"
    ]


def test_returns_field_names_not_values() -> None:
    card = _card(
        role="Autore di my-lib",
        profile_summary="Su GitHub come ada-codes.",
        location="mario.dev",
        industries=["Globex"],
    )

    hit = identifying(card, KNOWN)

    assert hit == ["role", "profile_summary", "industries", "location"]
    joined = " ".join(hit).lower()
    for identifier in ("my-lib", "ada", "mario", "globex"):
        assert identifier not in joined


def test_each_field_is_named_once() -> None:
    card = _card(profile_summary="my-lib, billing_engine e ada-codes.")
    assert identifying(card, KNOWN) == ["profile_summary"]


def test_the_person_with_no_source_is_the_old_guard() -> None:
    known = KnownIdentifiers.empty("Conti", "Ada")
    assert identifying(_card(profile_summary="La dashboard dei conti di una banca."), known) == []
    assert identifying(_card(profile_summary="Il profilo di Conti."), known) == ["profile_summary"]
    assert identifying(_card(profile_summary="Il profilo di CONTI."), known) == ["profile_summary"]
    assert identifying(_card(skills=["Python", "Conti"]), known) == ["skills"]
    assert identifying(_card(location="Conti"), known) == []  # a place is the CV's own
    assert identifying(_card(languages=["Conti"]), known) == []
    assert identifying(_card(profile_summary="Ada, backend developer."), known) == [
        "profile_summary"
    ]
    assert identifying(_card(skills=["Ada", "SPARK"]), known) == []
    assert identifying(_card(profile_summary="Un Adapter per ogni sensore."), known) == []
    assert identifying(_card(profile_summary="Scrivete a ada@studio.it."), known) == [
        "profile_summary"
    ]
    assert identifying(_card(skills=["Python", "www.ada.dev"]), known) == ["skills"]
    assert identifying(_card(location="https://maps.example/torino"), known) == ["location"]


def test_the_first_name_inside_the_persons_own_skill_is_the_skill() -> None:
    known = KnownIdentifiers.empty("Neri", "Ruby")
    skills = ["Ruby on Rails", "PostgreSQL"]
    card = _card(role="Ruby on Rails developer", skills=skills)
    assert identifying(card, known) == []
    assert identifying(_card(role="Ruby, backend developer", skills=skills), known) == ["role"]


def test_a_source_that_names_nothing_adds_nothing() -> None:
    known = KnownIdentifiers(last_name="Lovelace", github_login=None, repo_names=[], hostnames=[])
    assert identifying(_card(), known) == []
    assert identifying(_card(), KnownIdentifiers.empty()) == []


# ---- review of #671: the label without the TLD, ordinary words, the skill exemption -----


@pytest.mark.parametrize(
    ("host", "written"),
    [
        ("acme-labs.io", "Ha lavorato per Acme Labs."),
        ("acmelabs.com", "Scrive per Acmelabs."),
        ("mario-rossi.dev", "Il blog di Mario Rossi."),
        ("blog.acme-labs.co.uk", "Sviluppa per ACME-LABS."),
        ("mario.github.io", "Pagina personale di mario."),
    ],
)
def test_a_links_label_is_caught_without_its_tld(host: str, written: str) -> None:
    known = KnownIdentifiers(last_name="Zz", hostnames=[host])

    assert identifying(_card(profile_summary=written), known) == ["profile_summary"]


def test_a_platform_or_an_ordinary_label_is_not_a_leak() -> None:
    known = KnownIdentifiers(
        last_name="Zz", hostnames=["mario.github.io", "blog.medium.com", "website.dev"]
    )

    card = _card(profile_summary="Pubblica su GitHub e Medium, con un website e un blog.")

    assert identifying(card, known) == []


@pytest.mark.parametrize("name", ["website", "todo", "shop", "notes", "chat"])
def test_ordinary_repository_names_do_not_fail_every_card(name: str) -> None:
    known = KnownIdentifiers(last_name="Zz", repo_names=[name])

    assert identifying(_card(profile_summary=f"Costruisce un {name} e una chat."), known) == []


def test_a_login_or_a_label_equal_to_the_cards_own_skill_is_the_technology() -> None:
    skills = {"skills": ["Python", "Flask", "Rust"]}
    known = KnownIdentifiers(last_name="Zz", github_login="flask", hostnames=["rust.example"])

    assert (
        identifying(_card(profile_summary="Sviluppa API in Flask e Rust.", **skills), known) == []
    )
    # The same words with no such skill on the card are still the person.
    assert identifying(_card(profile_summary="Sviluppa API in Flask."), known) == [
        "profile_summary"
    ]


def test_a_clients_name_is_read_like_an_employer() -> None:
    known = KnownIdentifiers(last_name="Zz", employers=["Azienda Finta S.r.l."])

    assert identifying(_card(profile_summary="Un progetto per Azienda Finta."), known) == [
        "profile_summary"
    ]


def test_an_employer_that_is_also_a_technology_does_not_fail_the_skills() -> None:
    """Google, Microsoft, Oracle, SAP, Amazon: an employer's name is a skill too. A card
    that lists «Google Cloud» is no leak, in the skills or in a sentence that names it."""
    known = KnownIdentifiers(last_name="Zz", employers=["Google LLC"])
    skills = {"skills": ["Python", "Google Cloud"]}

    assert identifying(_card(**skills), known) == []
    assert (
        identifying(_card(profile_summary="Esperto di Google Cloud e Python.", **skills), known)
        == []
    )


def test_a_skill_that_is_an_employer_fails() -> None:
    """Only a skill that *is* the employer's name (legal form and punctuation aside) is a
    leak; one that merely contains it is the technology."""
    known = KnownIdentifiers(last_name="Zz", employers=["Initech Srl", "Google LLC"])

    assert identifying(_card(skills=["Initech"]), known) == ["skills"]
    assert identifying(_card(skills=["Python", "INITECH S.r.l."]), known) == ["skills"]
    assert identifying(_card(skills=["Google"]), known) == ["skills"]
    assert identifying(_card(skills=["Python", "Google Cloud"]), known) == []


def test_an_employer_in_the_role_the_summary_or_the_sectors_still_fails() -> None:
    known = KnownIdentifiers(last_name="Zz", employers=["Google LLC"])
    skills = {"skills": ["Python", "Google Cloud"]}

    assert identifying(_card(profile_summary="Lavora in Google da anni.", **skills), known) == [
        "profile_summary"
    ]
    assert identifying(_card(role="Developer in Google", **skills), known) == ["role"]
    assert identifying(_card(industries=["fintech", "Google"], **skills), known) == ["industries"]
    # Blanking the skill leaves the employer standing where the sentence says it twice.
    both = _card(profile_summary="Google Cloud in Google.", **skills)
    assert identifying(both, known) == ["profile_summary"]
