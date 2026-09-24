"""Typeset the freelancer contracts into PDFs, on a laptop.

    uv run python projects/hub/tools/build_contract_pdf.py
    uv run python projects/hub/tools/build_contract_pdf.py lettera-di-incarico --data job.json

Since REB-387 the texts, the Typst template and rebase's defaults are package data of
`rebase_core.contracts`, which the hub's API renders at request time; this script is the
thin wrapper that keeps a preview on a laptop. With no argument it builds both documents
as blank forms; naming one or more (by stem) builds only those. `--data` fills the
fields from a JSON object, read over the package's `rebase.json` and over
`content/contratti/rebase.local.json` when that file exists: git ignores it, and it holds
the real data of whoever signs for rebase today, which a public repository does not
print. `--public` leaves it out and writes to `dist/public/`. The PDFs land in
`content/contratti/dist/`, which git ignores too.
"""

import argparse
import sys
from pathlib import Path

from rebase_core.contracts.fields import ContractFailed, Value, merge_data, read_layer
from rebase_core.contracts.render import DOCUMENTS, company_defaults, render

HUB = Path(__file__).resolve().parent.parent
REPO = HUB.parent.parent
CONTRACTS = HUB / "content" / "contratti"
COMPANY_LOCAL = CONTRACTS / "rebase.local.json"
OUTPUT = CONTRACTS / "dist"


def load_data(path: Path | None, local: bool = True) -> dict[str, Value]:
    """rebase's defaults, then the local signer file, then the caller's.

    `null` means not known yet, and a later layer's `null` blanks an earlier value."""
    layers = [company_defaults()]
    for source in (COMPANY_LOCAL if local and COMPANY_LOCAL.is_file() else None, path):
        if source is None:
            continue
        try:
            text = source.read_text(encoding="utf-8")
        except OSError as exc:
            raise ContractFailed(f"cannot read {source}: {exc}") from exc
        layers.append(read_layer(text, str(source)))
    return merge_data(*layers)


def documents(names: list[str]) -> list[str]:
    if not names:
        return list(DOCUMENTS)
    unknown = [name for name in names if name not in DOCUMENTS]
    if unknown:
        raise ContractFailed(
            f"no such document: {', '.join(unknown)} (have: {', '.join(DOCUMENTS)})"
        )
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("documents", nargs="*", help="document stems; all if none")
    parser.add_argument("--data", type=Path, help="JSON object of field: value to fill in")
    parser.add_argument(
        "--public",
        action="store_true",
        help=f"leave out {COMPANY_LOCAL.name}, and write to dist/public unless --out says",
    )
    parser.add_argument(
        "--out", type=Path, help=f"directory for the PDFs (default {OUTPUT.relative_to(REPO)})"
    )
    args = parser.parse_args(argv)
    out: Path = args.out or (OUTPUT / "public" if args.public else OUTPUT)
    try:
        data = load_data(args.data, local=not args.public)
        out.mkdir(parents=True, exist_ok=True)
        for document in documents(args.documents):
            result = render(document, data)
            suffix = f"-{args.data.stem}" if args.data else ""
            target = out / f"{document}{suffix}.pdf"
            target.write_bytes(result.pdf)
            print(f"{target}: {len(result.pdf)} bytes")
            if args.data and result.blank:
                print(f"  left blank: {', '.join(result.blank)}")
        return 0
    except ContractFailed as exc:
        print(f"build_contract_pdf: {exc.detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
