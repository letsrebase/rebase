"""The API image carries the renderer PigroCRM's image carries, at the same versions, and
the two brand files the contracts are typeset in (REB-387)."""

import re
from pathlib import Path

from rebase_core.contracts import brand

REPO = Path(__file__).resolve().parents[5]
HUB_IMAGE = REPO / "projects" / "hub" / "Dockerfile.api"
CRM_IMAGE = REPO / "projects" / "pigrocrm" / "Dockerfile.api"


def _pins(dockerfile: Path) -> dict[str, str]:
    text = dockerfile.read_text(encoding="utf-8")
    return dict(re.findall(r"^ARG (PANDOC_VERSION|TYPST_VERSION)=(\S+)$", text, re.M))


def test_the_hub_image_pins_the_renderer_pigrocrm_was_verified_against() -> None:
    assert (
        _pins(HUB_IMAGE)
        == _pins(CRM_IMAGE)
        == {
            "PANDOC_VERSION": "3.8.2.1",
            "TYPST_VERSION": "0.14.2",
        }
    )


def test_the_hub_image_copies_the_brand_where_the_renderer_reads_it() -> None:
    text = HUB_IMAGE.read_text(encoding="utf-8")
    for source in (brand.PALETTE, brand.FONT):
        path = str(source.relative_to(REPO))
        assert f"COPY {path} {path}" in text, path
    assert brand.REPO == REPO
