"""The official FPR12 schema, loaded with its one remote import resolved locally.

`Schema_VFPR12_v1.2.3.xsd` carries exactly one `xs:import`, pointing at
`http://www.w3.org/TR/2002/REC-xmldsig-core-20020212/xmldsig-core-schema.xsd`. A build
run has no network, so the import is redirected to the vendored sibling by an lxml
resolver rather than by editing the schema.

Validation goes through `lxml.etree.XMLSchema` rather than the `xmllint --schema` the
spec's success criterion names. Two reasons, both practical: nothing in this repo
installs `libxml2-utils` (`Dockerfile.api` installs Pandoc, Typst and `libpq5`), and
`lxml` is already a dependency because the generator needs it -- so this is the same
libxml2 engine `xmllint` is a thin CLI over, with no new binary in the image.

Version 1.2.3, not the 1.2.1 the spec names: 1.2.3 is the ordinary-invoice schema
in force since 2025-04-01 and 1.2.1 is no longer published. The `versione="FPR12"`
attribute is unchanged across 1.2.x, so nothing about the generator differs; proving a
file valid against a superseded rule set would simply prove the wrong thing.
"""

from functools import lru_cache
from pathlib import Path

from lxml import etree

HERE = Path(__file__).resolve().parent
FPR12_XSD = HERE / "Schema_VFPR12_v1.2.3.xsd"
XMLDSIG_XSD = HERE / "xmldsig-core-schema.xsd"
XMLDSIG_URL = "http://www.w3.org/TR/2002/REC-xmldsig-core-20020212/xmldsig-core-schema.xsd"


class _LocalXmldsigResolver(etree.Resolver):  # type: ignore[misc]
    def resolve(self, system_url: str, public_id: str | None, context: object) -> object:
        if system_url == XMLDSIG_URL:
            return self.resolve_filename(str(XMLDSIG_XSD), context)
        return None


@lru_cache(maxsize=1)
def fpr12_schema() -> etree.XMLSchema:
    """The compiled schema. Cached because compiling it takes noticeable time and
    every validating test wants the same object."""
    parser = etree.XMLParser(no_network=True, load_dtd=False, resolve_entities=True)
    parser.resolvers.add(_LocalXmldsigResolver())
    return etree.XMLSchema(etree.parse(str(FPR12_XSD), parser))


def assert_valid(xml_bytes: bytes) -> None:
    """Validate, and on failure raise with libxml2's own message.

    A bare `assert schema.validate(doc)` reports "False", which says nothing about
    which element in a 200-line sequence is out of order -- and element order is the
    single hardest thing about FPR12.
    """
    schema = fpr12_schema()
    document = etree.fromstring(xml_bytes)
    if not schema.validate(document):
        raise AssertionError(
            "l'XML non valida contro lo schema FPR12 v1.2.3:\n"
            + "\n".join(str(error) for error in schema.error_log)
        )
