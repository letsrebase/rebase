"""The anonymous CV's PDF (REB-862, spec § The PDF): Typst over the body as JSON, in the
contracts' brand. What can go wrong without anybody noticing is a role or a section the
page leaves out, a heading in the wrong language, a CV whose text is read as Typst markup
(and either breaks the build or rewrites the page), and two downloads of one body that are
not the same file. The text is read back with pypdf, as a company's ATS would read it.

The renders need `typst` (and fontTools for the brand's Outfit), which every machine that
runs the hub's suite has (`renderer: true` in ci.yml); without it they skip, and the
filename's rule still runs."""

import re
import shutil
from io import BytesIO

import pytest
from pypdf import PdfReader

from rebase_core.anonymous_cv.render import (
    MODALITA_LABELS,
    CvHead,
    CvPrintFailed,
    FakeCvRenderer,
    TypstCvRenderer,
    filename,
    head_line,
)
from rebase_core.anonymous_cv.schema import (
    AnonymousCv,
    Esperienza,
    Gruppo,
    Lingua,
    Punto,
    Sezione,
    Titolo,
)
from rebase_core.contracts.fields import ContractFailed

needs_typst = pytest.mark.skipif(shutil.which("typst") is None, reason="typst is not installed")


def _bullet(testo: str, titolo: str | None = None) -> Punto:
    return Punto(titolo=titolo, testo=testo)


# A long, ordinary two-page CV: a freelance role whose engagements are bold leads, four
# employed roles, more skill areas than fit on a line each, two degrees, five
# certifications, three languages and one section of its own.
CV = AnonymousCv(
    lingua="it",
    titolo="Oracle DBA e Security Engineer",
    sommario=(
        "Database administrator e security engineer con dodici anni su Oracle in banche, "
        "telecomunicazioni e pubblica amministrazione. Ha guidato migrazioni a Oracle 19c "
        "di decine di database critici, progettato piani di disaster recovery provati sul "
        "campo e condotto audit ISO 27001 e assessment di sicurezza per clienti enterprise. "
        "Lavora bene tra team di sviluppo, sicurezza e audit, e scrive procedure che "
        "restano in uso dopo il suo passaggio."
    ),
    esperienze=[
        Esperienza(
            ruolo="Security Consultant e Oracle DBA (libero professionista)",
            azienda="Consulenza indipendente",
            luogo="Remoto",
            periodo="Luglio 2022 – oggi",
            punti=[
                _bullet(
                    "Mappatura dei controlli ISO 27001 e NIST CSF sui processi IT, gap "
                    "analysis e piano di rimedio approvato dalla direzione sicurezza.",
                    "GRC Analyst (TIM S.p.A.)",
                ),
                _bullet(
                    "Preparazione delle evidenze per l'audit di certificazione e "
                    "accompagnamento degli auditor esterni.",
                    "GRC Analyst (TIM S.p.A.)",
                ),
                _bullet(
                    "Gestione di 40 istanze Oracle 19c in RAC e Data Guard, patching "
                    "trimestrale senza fermi e tuning delle query più costose.",
                    "Oracle DBA (Banca Meridiana S.p.A.)",
                ),
                _bullet(
                    "Hardening dei database secondo i CIS Benchmark e revisione dei "
                    "privilegi degli utenti applicativi su 120 schemi.",
                    "Security Assessment (Nexa Consulting S.r.l.)",
                ),
                _bullet(
                    "Valutazione della postura di sicurezza di un fornitore cloud per un "
                    "cliente del settore assicurativo, con report per il comitato rischi.",
                    "Vendor Risk Assessment (Assicurazioni Adriatiche S.p.A.)",
                ),
                _bullet("Formazione interna sui controlli di sicurezza per i team DBA."),
            ],
        ),
        Esperienza(
            ruolo="Senior Oracle DBA",
            azienda="Banca Meridiana S.p.A.",
            luogo="Milano, IT",
            periodo="Marzo 2018 – Giugno 2022",
            punti=[
                _bullet(
                    "Migrazione di 25 database da Oracle 11g a 19c con downtime sotto le "
                    "due ore per ciascuno, pianificata con i team applicativi."
                ),
                _bullet(
                    "Progettazione del disaster recovery con Data Guard tra due data "
                    "center, provato ogni sei mesi con il passaggio reale del servizio."
                ),
                _bullet("Automazione dei backup RMAN e dei controlli giornalieri con Ansible."),
                _bullet("Supporto agli audit di Banca d'Italia sui sistemi di core banking."),
                _bullet(
                    "Introduzione di Transparent Data Encryption e Database Vault sui "
                    "database con dati di pagamento."
                ),
                _bullet("Coordinamento di tre DBA junior e dei turni di reperibilità."),
            ],
        ),
        Esperienza(
            ruolo="Database Administrator",
            azienda="Telco Servizi S.r.l.",
            luogo="Roma, IT",
            periodo="Settembre 2014 – Febbraio 2018",
            punti=[
                _bullet("Amministrazione di Oracle 11g e PostgreSQL per il billing."),
                _bullet(
                    "Riduzione del 40% dei tempi di chiusura mensile con partizionamento e "
                    "indici mirati."
                ),
                _bullet("Monitoraggio con Oracle Enterprise Manager e Zabbix."),
                _bullet(
                    "Consolidamento di 12 istanze su un cluster RAC a due nodi, con un "
                    "risparmio di licenze stimato nel 30%."
                ),
                _bullet("Scrittura del runbook per gli incidenti sui database di produzione."),
            ],
        ),
        Esperienza(
            ruolo="Analista programmatore PL/SQL",
            azienda="Regione Campania – Direzione Sistemi Informativi",
            luogo="Napoli, IT",
            periodo="Settembre 2012 – Agosto 2014",
            punti=[
                _bullet(
                    "Sviluppo di procedure PL/SQL per il sistema dei tributi regionali e "
                    "dei relativi report."
                ),
                _bullet("Ottimizzazione dei batch notturni, passati da sei a due ore."),
                _bullet("Supporto agli utenti degli uffici nella fase di avvio."),
            ],
        ),
        Esperienza(
            ruolo="Junior DBA",
            azienda="Sistemi Informativi Sud S.r.l.",
            luogo="Napoli, IT",
            periodo="Gennaio 2012 – Agosto 2012",
            punti=[
                _bullet("Installazione e patching di Oracle 10g e 11g."),
                _bullet("Script di manutenzione in Bash e PL/SQL."),
            ],
        ),
    ],
    competenze=[
        Gruppo(area="Database", voci=["Oracle 11g–19c", "RAC", "Data Guard", "PostgreSQL"]),
        Gruppo(
            area="Sicurezza",
            voci=["ISO 27001", "NIST CSF", "CIS Benchmark", "IAM", "Database Vault", "TDE"],
        ),
        Gruppo(area="Backup e DR", voci=["RMAN", "Data Guard", "Zero Data Loss"]),
        Gruppo(area="Cloud", voci=["Oracle Cloud Infrastructure", "AWS RDS", "Azure"]),
        Gruppo(area="Sistemi operativi", voci=["Oracle Linux", "Red Hat", "AIX"]),
        Gruppo(area="Linguaggi", voci=["SQL", "PL/SQL", "Bash", "Python"]),
        Gruppo(area="Automazione", voci=["Ansible", "Terraform", "Jenkins"]),
        Gruppo(area="Monitoraggio", voci=["Oracle Enterprise Manager", "Zabbix", "Grafana"]),
        Gruppo(area="Virtualizzazione", voci=["VMware vSphere", "Oracle VM"]),
        Gruppo(area="Networking", voci=["TCP/IP", "VPN", "Firewall"]),
        Gruppo(
            area="Standard e framework",
            voci=["ITIL", "COBIT", "GDPR", "PCI DSS", "Circolare 285 di Banca d'Italia"],
        ),
        Gruppo(area="Strumenti", voci=["Jira", "Confluence", "ServiceNow", "Git"]),
        Gruppo(area="Metodologie", voci=["Agile", "Scrum", "Change management"]),
    ],
    formazione=[
        Titolo(
            titolo="Laurea magistrale in Ingegneria Informatica",
            istituto="Università degli Studi di Napoli Federico II",
            luogo="Napoli, IT",
            periodo="2009 – 2011",
        ),
        Titolo(
            titolo="Laurea triennale in Ingegneria Informatica",
            istituto="Università degli Studi di Napoli Federico II",
            luogo="Napoli, IT",
            periodo="2006 – 2009",
        ),
    ],
    certificazioni=[
        "Oracle Certified Professional (OCP) 19c",
        "CISSP",
        "ISO/IEC 27001 Lead Auditor",
        "AWS Certified Solutions Architect – Associate",
        "ITIL 4 Foundation",
    ],
    lingue=[
        Lingua(lingua="Italiano", livello="Madrelingua"),
        Lingua(lingua="Inglese", livello="C1"),
        Lingua(lingua="Spagnolo", livello="B1"),
    ],
    altre_sezioni=[
        Sezione(
            titolo="Pubblicazioni",
            voci=[
                "Data Guard in pratica, articolo per una rivista di settore (2021)",
                "Intervento sul patching senza fermi a un convegno di utenti Oracle (2019)",
            ],
        )
    ],
)

HEAD = CvHead(seniority="senior", anni=12, luogo="Roma", modalita="ibrido", ref="d41c7e2a")


def _text(pdf: bytes) -> str:
    """The PDF's text as one line of single spaces: a wrapped line reads as one sentence."""
    pages = PdfReader(BytesIO(pdf)).pages
    return " ".join(" ".join(page.extract_text() for page in pages).split())


def _heading(word: str) -> str:
    """A heading as the PDF's text reads it: Outfit has no small capitals, so the page
    draws them as capitals of two sizes, which a text layer reads as capitals."""
    return word.upper()


@needs_typst
def test_every_role_and_section_prints() -> None:
    pdf = TypstCvRenderer().render(CV, HEAD)
    assert pdf.startswith(b"%PDF-")
    text = _text(pdf)
    assert "Oracle DBA e Security Engineer" in text
    assert "Senior · 12 anni di esperienza · Roma · Ibrido" in text
    for role in CV.esperienze:
        assert role.ruolo in text
        assert role.azienda is not None and role.azienda in text
        assert role.periodo is not None and role.periodo in text
    assert "GRC Analyst (TIM S.p.A.): Mappatura dei controlli ISO 27001" in text
    assert "Automazione dei backup RMAN e dei controlli giornalieri con Ansible." in text
    assert "Backup e DR: RMAN, Data Guard, Zero Data Loss" in text
    assert "Metodologie: Agile, Scrum, Change management" in text
    assert "Università degli Studi di Napoli Federico II" in text
    assert "Laurea magistrale in Ingegneria Informatica" in text
    assert "ISO/IEC 27001 Lead Auditor" in text
    assert "Spagnolo" in text and "B1" in text
    assert "Data Guard in pratica" in text
    for heading in (
        "Profilo",
        "Esperienze",
        "Competenze",
        "Formazione",
        "Certificazioni",
        "Lingue",
        "Pubblicazioni",
    ):
        assert _heading(heading) in text, heading
    assert "Profilo proposto da rebase · ciao@letsrebase.com · letsrebase.com" in text
    assert "Rif. d41c7e2a" in text


@needs_typst
def test_the_fixture_fills_two_a4_pages_and_every_page_has_the_footer() -> None:
    reader = PdfReader(BytesIO(TypstCvRenderer().render(CV, HEAD)))
    assert len(reader.pages) == 2
    for number, page in enumerate(reader.pages, start=1):
        assert round(float(page.mediabox.width)) == 595
        assert round(float(page.mediabox.height)) == 842
        text = " ".join(page.extract_text().split())
        assert "Profilo proposto da rebase" in text
        assert "Rif. d41c7e2a" in text
        assert f"{number} / 2" in text


@needs_typst
def test_headings_follow_lingua() -> None:
    text = _text(TypstCvRenderer().render(CV.model_copy(update={"lingua": "en"}), HEAD))
    for english, italian in (
        ("Profile", "Profilo"),
        ("Experience", "Esperienze"),
        ("Skills", "Competenze"),
        ("Education", "Formazione"),
        ("Certifications", "Certificazioni"),
        ("Languages", "Lingue"),
    ):
        assert _heading(english) in text, english
        assert _heading(italian) not in text, italian
    assert "Senior · 12 years of experience · Roma · Ibrido" in text


@needs_typst
def test_empty_sections_are_left_out() -> None:
    bare = CV.model_copy(
        update={"certificazioni": [], "altre_sezioni": [], "lingue": [], "formazione": []}
    )
    text = _text(TypstCvRenderer().render(bare, HEAD))
    assert _heading("Esperienze") in text
    for heading in ("Certificazioni", "Lingue", "Formazione", "Pubblicazioni"):
        assert _heading(heading) not in text, heading


@needs_typst
def test_markup_prints_literally() -> None:
    markup = (
        "#set page(width: 1cm)",
        "*bold*",
        "$x^2$",
        "@ref",
        "<label>",
        "// comment",
        "\\",
        '#eval("1 + 1")',
        "] #text(fill: red)[",
    )
    cv = CV.model_copy(deep=True)
    cv.titolo = f"Titolo {markup[0]}"
    cv.sommario = " ".join(f"«{piece}»" for piece in markup)
    cv.esperienze[0].ruolo = f"Ruolo {markup[1]}"
    cv.esperienze[0].punti[0].titolo = f"Lead {markup[2]}"
    cv.esperienze[0].punti[0].testo = f"Testo {markup[3]} {markup[4]}"
    cv.competenze[0].area = f"Area {markup[5]}"
    cv.competenze[0].voci = [f"Voce {markup[6]}"]
    cv.certificazioni = [f"Cert {markup[7]}"]
    cv.lingue = [Lingua(lingua=f"Lingua {markup[8]}", livello="C1")]
    pdf = TypstCvRenderer().render(cv, HEAD)
    reader = PdfReader(BytesIO(pdf))
    assert round(float(reader.pages[0].mediabox.width)) == 595
    text = _text(pdf)
    for piece in markup:
        assert f"«{piece}»" in text, piece
    assert "Titolo #set page(width: 1cm)" in text
    assert "Ruolo *bold*" in text
    assert "Lead $x^2$: Testo @ref <label>" in text
    assert "Area // comment: Voce \\" in text
    assert 'Cert #eval("1 + 1")' in text
    assert "Lingua ] #text(fill: red)[" in text


@needs_typst
def test_same_body_same_bytes() -> None:
    renderer = TypstCvRenderer()
    assert renderer.render(CV, HEAD) == renderer.render(CV, HEAD)


@needs_typst
def test_a_head_with_nothing_known_prints_only_the_title() -> None:
    head = CvHead(seniority=None, anni=None, luogo=None, modalita=None, ref="d41c7e2a")
    text = _text(TypstCvRenderer().render(CV, head))
    assert "Oracle DBA e Security Engineer" in text
    assert "Senior ·" not in text
    assert "12 anni di esperienza" not in text
    assert "Ibrido" not in text


def test_head_line() -> None:
    assert head_line(HEAD, "it") == "Senior · 12 anni di esperienza · Roma · Ibrido"
    assert head_line(HEAD, "en") == "Senior · 12 years of experience · Roma · Ibrido"
    one = CvHead(seniority="lead", anni=1, luogo=None, modalita="remoto", ref="d41c7e2a")
    assert head_line(one, "it") == "Lead · 1 anno di esperienza · Da remoto"
    assert head_line(one, "en") == "Lead · 1 year of experience · Da remoto"
    none = CvHead(seniority="junior", anni=0, luogo=" ", modalita="in_sede", ref="d41c7e2a")
    assert head_line(none, "it") == "Junior · meno di un anno di esperienza · In sede"
    assert head_line(none, "en") == "Junior · less than a year of experience · In sede"
    empty = CvHead(seniority=None, anni=None, luogo=None, modalita=None, ref="d41c7e2a")
    assert head_line(empty, "it") == ""
    assert MODALITA_LABELS == {"remoto": "Da remoto", "ibrido": "Ibrido", "in_sede": "In sede"}


def _with_titolo(titolo: str) -> AnonymousCv:
    return CV.model_copy(update={"titolo": titolo})


def test_filename() -> None:
    assert (
        filename(_with_titolo("Oracle DBA & Security Engineer"), "d41c7e2a")
        == "rebase-cv-oracle-dba-security-engineer-d41c7e2a.pdf"
    )
    assert (
        filename(_with_titolo("Responsabile qualità e Sécurité"), "d41c7e2a")
        == "rebase-cv-responsabile-qualita-e-securite-d41c7e2a.pdf"
    )
    long = filename(
        _with_titolo("Senior Full-Stack Developer and Cloud Architect for Payments"), "d41c7e2a"
    )
    slug = re.fullmatch(r"rebase-cv-([a-z0-9-]+)-d41c7e2a\.pdf", long)
    assert slug is not None
    assert len(slug.group(1)) <= 40
    assert slug.group(1) == "senior-full-stack-developer-and-cloud"
    assert filename(_with_titolo("— 日本語 —"), "d41c7e2a") == "rebase-cv-d41c7e2a.pdf"


def test_no_typst_is_the_print_failure_sentence(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `ContractFailed`, so the API answers it as a 503 with the sentence, never Typst's."""
    monkeypatch.setenv("PATH", "")
    with pytest.raises(CvPrintFailed) as failed:
        TypstCvRenderer().render(CV, HEAD)
    assert isinstance(failed.value, ContractFailed)
    assert failed.value.message == "Non riesco a stampare il CV anonimo."
    assert str(failed.value) == "Non riesco a stampare il CV anonimo."


def test_the_fake_records_and_answers_a_pdf() -> None:
    fake = FakeCvRenderer()
    assert fake.render(CV, HEAD) == b"%PDF-fake"
    assert fake.calls == [(CV, HEAD)]
