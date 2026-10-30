"""The anonymous CV's identity guard (REB-862): what it refuses, and what a CV may say."""

from rebase_core.anonymous_cv.guard import Who, identifying_fields, who_of
from rebase_core.anonymous_cv.schema import (
    AnonymousCv,
    Esperienza,
    Gruppo,
    Lingua,
    Punto,
    Sezione,
    Titolo,
    strings,
)

CV = AnonymousCv(
    lingua="it",
    titolo="Backend developer",
    sommario="Sviluppatore con dieci anni di esperienza su sistemi distribuiti.",
    esperienze=[
        Esperienza(
            ruolo="GRC Analyst",
            azienda="TIM S.p.A.",
            luogo="Messina, IT",
            periodo="2019 – oggi",
            punti=[
                Punto(titolo="Audit (TIM S.p.A.)", testo="Ha condotto audit ISO 27001."),
                Punto(titolo=None, testo="Ha scritto le policy di sicurezza."),
            ],
        ),
        Esperienza(
            ruolo="Sviluppatore",
            azienda="Acme S.r.l.",
            luogo="Roma, IT",
            periodo="2014 – 2019",
            punti=[
                Punto(titolo=None, testo="Ha portato in produzione il servizio di fatturazione.")
            ],
        ),
    ],
    competenze=[Gruppo(area="Linguaggi", voci=["Python", "Go"])],
    formazione=[
        Titolo(
            titolo="Diploma di perito informatico",
            istituto="I.I.S. Ettore Majorana",
            luogo="Messina, IT",
            periodo="2009 – 2014",
        )
    ],
    certificazioni=["CISA"],
    lingue=[
        Lingua(lingua="Italiano", livello="Madrelingua"),
        Lingua(lingua="Inglese", livello=None),
    ],
    altre_sezioni=[Sezione(titolo="Progetti", voci=["Un parser di fatture"])],
)

WHO = who_of(
    nome="Mario",
    cognome="Messina",
    emails=["mario.messina@proton.me"],
    phones=["+39 333 123 4567"],
    links=[
        "https://github.com/mmessina",
        "https://mariomessina.dev",
        "https://www.linkedin.com/in/mario-messina",
    ],
)


def test_a_clean_cv_passes() -> None:
    assert identifying_fields(CV, WHO) == []


def test_surname_in_a_bullet() -> None:
    cv = CV.model_copy(deep=True)
    cv.esperienze[1].punti[0].testo = "Ha lavorato con Messina su fatturazione."
    assert identifying_fields(cv, WHO) == ["esperienze[1].punti[0].testo"]


def test_surname_in_capitals_in_the_title() -> None:
    cv = CV.model_copy(update={"titolo": "MARIO MESSINA – Backend developer"})
    assert "titolo" in identifying_fields(cv, WHO)


def test_surname_in_a_company() -> None:
    cv = CV.model_copy(deep=True)
    cv.esperienze[0].azienda = "Messina Consulting S.r.l."
    assert identifying_fields(cv, WHO) == ["esperienze[0].azienda"]


def test_lowercase_word_is_not_the_surname() -> None:
    who = who_of(nome="Mario", cognome="Porta", emails=[], phones=[], links=[])
    cv = CV.model_copy(deep=True)
    cv.esperienze[1].punti[0].testo = "Ha portato in produzione, porta in produzione."
    assert identifying_fields(cv, who) == []


def test_first_name_that_is_a_skill() -> None:
    who = who_of(nome="Ada", cognome=None, emails=[], phones=[], links=[])
    cv = CV.model_copy(update={"sommario": "Sviluppo in Ada"})
    cv.competenze = [Gruppo(area="Linguaggi", voci=["Ada"])]
    assert identifying_fields(cv, who) == []
    other = CV.model_copy(update={"sommario": "Ada ha guidato il team"})
    assert identifying_fields(other, who) == ["sommario"]


def test_first_name_is_not_read_in_a_place_or_a_school() -> None:
    who = who_of(nome="Roma", cognome=None, emails=[], phones=[], links=[])
    assert identifying_fields(CV, who) == []


def test_phone_in_another_format() -> None:
    cv = CV.model_copy(deep=True)
    cv.esperienze[0].punti[1].testo = "chiamare 333 1234567"
    assert identifying_fields(cv, WHO) == ["esperienze[0].punti[1].testo"]


def test_short_number_is_not_a_phone() -> None:
    who = who_of(nome=None, cognome=None, emails=[], phones=["12345"], links=[])
    cv = CV.model_copy(update={"sommario": "Ha seguito 12345 utenti."})
    assert identifying_fields(cv, who) == []


def test_email_handle_and_login() -> None:
    for word in ("mmessina", "mario.messina"):
        cv = CV.model_copy(deep=True)
        cv.altre_sezioni[0].voci[0] = word
        assert identifying_fields(cv, WHO) == ["altre_sezioni[0].voci[0]"]


def test_any_url_or_at() -> None:
    for word in ("www.github.com/acme", "https://github.com/acme", "@acme"):
        cv = CV.model_copy(deep=True)
        cv.altre_sezioni[0].voci[0] = word
        assert identifying_fields(cv, WHO) == ["altre_sezioni[0].voci[0]"]


def test_personal_domain_whole_host() -> None:
    cv = CV.model_copy(deep=True)
    cv.altre_sezioni[0].voci[0] = "mariomessina.dev"
    assert identifying_fields(cv, WHO) == ["altre_sezioni[0].voci[0]"]


def test_a_host_label_is_not_the_host() -> None:
    who = who_of(nome=None, cognome=None, emails=[], phones=[], links=["https://acme.io"])
    cv = CV.model_copy(deep=True)
    cv.esperienze[1].azienda = "Acme S.r.l."
    assert identifying_fields(cv, who) == []


def test_merged_unites_without_repeating() -> None:
    other = who_of(
        nome="Mario", cognome="Messina", emails=["MARIO.MESSINA@proton.me"], phones=[], links=[]
    )
    merged = WHO.merged(other)
    assert merged.nomi == ["Mario"] and merged.emails == ["mario.messina@proton.me"]
    assert isinstance(merged, Who) and merged.github_login == "mmessina"


def test_strings_paths_in_order() -> None:
    assert [path for path, _ in strings(CV)][:3] == ["titolo", "sommario", "esperienze[0].ruolo"]


def _voce(value: str) -> AnonymousCv:
    cv = CV.model_copy(deep=True)
    cv.altre_sezioni[0].voci[0] = value
    return cv


def test_bare_profile_link() -> None:
    who = who_of(nome=None, cognome=None, emails=[], phones=[], links=[])
    for word in ("linkedin.com/in/someone-else", "github.com/acme", "Github.com/Acme"):
        assert identifying_fields(_voce(word), who) == ["altre_sezioni[0].voci[0]"]
    assert identifying_fields(_voce("GitHub Actions e LinkedIn"), who) == []


def test_name_inside_a_slug() -> None:
    who = who_of(nome="Alex", cognome="Fegatilli", emails=[], phones=[], links=[])
    for word in ("alex-fegatilli-fau50", "fegatilli.dev"):
        assert identifying_fields(_voce(word), who) == ["altre_sezioni[0].voci[0]"]


def test_skills_with_dots_and_slashes_pass() -> None:
    cv = CV.model_copy(deep=True)
    cv.competenze = []
    cv.esperienze[1].punti[0].testo = "migrato da Node.js/Express ad ASP.NET Core"
    assert identifying_fields(cv, WHO) == []
