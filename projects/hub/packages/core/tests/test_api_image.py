"""The API image carries the renderer PigroCRM's image carries, at the same versions, and
the brand files the contracts are typeset in (REB-387): the palette, the typeface and,
since REB-479, the echo logo. The Nix `hub-api` package carries the same files where
`REBASE_CONTRACTS_BRAND_DIR` points (REB-403)."""

import re
from pathlib import Path

from rebase_core.contracts import brand

REPO = Path(__file__).resolve().parents[5]
HUB_IMAGE = REPO / "projects" / "hub" / "Dockerfile.api"
CRM_IMAGE = REPO / "projects" / "pigrocrm" / "Dockerfile.api"
FLAKE = REPO / "flake.nix"
# Every file `rebase_core.contracts.brand` reads.
BRAND_FILES = (brand.PALETTE, brand.FONT, brand.ECHO)


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
    for source in BRAND_FILES:
        path = str(source.relative_to(REPO))
        assert f"COPY {path} {path}" in text, path
    assert brand.REPO == REPO


def test_the_nix_package_installs_the_brand_where_the_renderer_reads_it() -> None:
    """`hub-api` installs each brand file under `share/hub-api/brand` at its path inside
    `shared/brand`, which is where `brand` looks once the wrapper sets the directory."""
    text = FLAKE.read_text(encoding="utf-8")
    for source in BRAND_FILES:
        inside = source.relative_to(brand.BRAND).as_posix()
        assert f"${{./shared/brand/{inside}}}" in text, inside
        assert f'"$out/share/hub-api/brand/{inside}"' in text, inside
