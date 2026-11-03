"""Tests of the English lint: a planted Italian name fails it, product copy does not.

Run: python3 -m unittest discover -s tooling/english-lint
The TypeScript cases need `node` and the `typescript` package (`pnpm install`).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import english_lint  # noqa: E402
import vocabulary  # noqa: E402

TYPESCRIPT_READY = (
    shutil.which("node") is not None and (HERE / "node_modules" / "typescript").exists()
)


class Tree(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def write(self, path: str, text: str) -> str:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf8")
        return path

    def lint(self, *paths: str) -> list[english_lint.Finding]:
        return english_lint.lint(self.root, list(paths))

    def words(self, *paths: str) -> set[tuple[str, str]]:
        return {(f.kind, f.word) for f in self.lint(*paths)}


class PythonRules(Tree):
    def test_planted_italian_identifier_fails(self) -> None:
        path = self.write("a/service.py", "def create_fattura(customer):\n    return customer\n")
        self.assertIn(("identifier", "fattura"), self.words(path))

    def test_every_form_of_a_name_is_split(self) -> None:
        path = self.write(
            "a/names.py",
            "ClienteSchema = 1\nSCADENZA_MAX = 2\nlista_clienti = 3\nrenewalAzienda = 4\n",
        )
        found = {w for _, w in self.words(path)}
        self.assertEqual(found, {"cliente", "scadenza", "lista", "clienti", "azienda"})

    def test_english_code_passes(self) -> None:
        path = self.write(
            "a/ok.py",
            '"""Create an invoice for a customer."""\n\n\n'
            "def issue(invoice, due_date):  # when it is payable\n    return invoice\n",
        )
        self.assertEqual(self.lint(path), [])

    def test_string_literals_are_product_copy_and_exempt(self) -> None:
        path = self.write(
            "a/copy.py",
            'MESSAGE = "Cliente non trovato: controlla la partita IVA"\n'
            'KEY = "{{cliente.provincia}}"\n'
            'SQL = f"ALTER TABLE attivita RENAME TO {new}"\n',
        )
        self.assertEqual(self.lint(path), [])

    def test_italian_comment_fails(self) -> None:
        path = self.write("a/c.py", "x = 1  # il totale della fattura non puo scendere\n")
        self.assertIn("comment", {k for k, _ in self.words(path)})

    def test_italian_docstring_fails(self) -> None:
        path = self.write(
            "a/d.py", 'def f():\n    """Calcola il totale della fattura per il cliente."""\n'
        )
        self.assertIn("comment", {k for k, _ in self.words(path)})

    def test_quoted_italian_in_an_english_comment_is_copy(self) -> None:
        path = self.write("a/q.py", 'x = 1  # the page says "Cliente non trovato" and `scadenza`\n')
        self.assertEqual(self.lint(path), [])

    def test_route_and_tool_docstrings_are_published_copy(self) -> None:
        path = self.write(
            "a/routes.py",
            "@router.get('/x')\n"
            "def read():\n"
            '    """Legge il totale della fattura per il cliente."""\n\n\n'
            "@mcp.tool()\n"
            "def tool():\n"
            '    """Restituisce il totale della fattura per il cliente."""\n',
        )
        self.assertEqual(self.lint(path), [])

    def test_a_quotation_wrapped_over_comment_lines_is_copy(self) -> None:
        path = self.write(
            "a/wrap.py",
            "# the sentence is «la bozza intatta con l'errore\n"
            "# accanto, il composer si riapre» in the spec\n"
            "x = 1\n",
        )
        self.assertEqual(self.lint(path), [])

    def test_italian_fiscal_terms_are_fine_in_english_prose(self) -> None:
        path = self.write(
            "a/fiscal.py", "x = 1  # the partita IVA and the codice fiscale are kept\n"
        )
        self.assertEqual(self.lint(path), [])

    def test_a_single_borrowed_word_is_not_a_comment_in_italian(self) -> None:
        path = self.write("a/b.py", "x = 1  # the scadenza is on the invoice\n")
        self.assertEqual(self.lint(path), [])


class PathsAndAllowlist(Tree):
    def test_italian_file_name_fails(self) -> None:
        path = self.write("a/test_fatture_api.py", "x = 1\n")
        self.assertIn(("path", "fatture"), self.words(path))

    def test_italian_directory_is_reported_once(self) -> None:
        paths = [self.write(f"a/clienti/m{i}.py", "x = 1\n") for i in range(3)]
        found = [f for f in self.lint(*paths) if f.kind == "path"]
        self.assertEqual([f.path for f in found], ["a/clienti"])

    def test_allowlist_keeps_a_redirect_stub_path(self) -> None:
        path = self.write("projects/pigrocrm/apps/web/src/routes/app/clienti.tsx", "export {}\n")
        self.assertEqual([f for f in self.lint(path) if f.kind == "path"], [])

    def test_allowlist_is_limited_to_its_words_and_kinds(self) -> None:
        directory = "projects/pigrocrm/packages/core/src/pigrocrm/core/invoices/fatturapa"
        tags = self.write(
            f"{directory}/a.py", "x = 1  # Sequence: Anagrafica, Divisa, Natura, Imposta\n"
        )
        other = self.write(
            f"{directory}/b.py", "x = 1  # Sequence: Anagrafica, Divisa, annullata, emessa\n"
        )
        name = self.write(f"{directory}/c.py", "cedente = 1\n")
        self.assertEqual(self.lint(tags), [])
        self.assertEqual({w for _, w in self.words(other)}, {"Anagrafica"})
        self.assertIn(("identifier", "cedente"), self.words(name))

    def test_proper_noun_is_not_an_italian_word(self) -> None:
        path = self.write("a/proper.py", "class FatturaPAExporter:\n    fatturapa_version = 1\n")
        self.assertEqual(self.lint(path), [])

    def test_alembic_history_is_skipped_and_new_revisions_are_not(self) -> None:
        directory = "projects/pigrocrm/packages/core/migrations/versions"
        old = self.write(f"{directory}/0067_old.py", "def upgrade():\n    fattura = 1\n")
        new = self.write(f"{directory}/0068_new.py", "def upgrade():\n    fattura = 1\n")
        self.assertEqual(self.lint(old), [])
        self.assertIn(("identifier", "fattura"), self.words(new))

    def test_hash_comments_of_config_files_are_read(self) -> None:
        path = self.write(
            "deploy/run.sh",
            "#!/bin/sh\n# il file deve essere letto dal server per la fattura\necho ok\n",
        )
        self.assertIn("comment", {k for k, _ in self.words(path)})


class TypeScriptRules(Tree):
    def setUp(self) -> None:
        super().setUp()
        if not TYPESCRIPT_READY:
            self.skipTest("needs node and `pnpm install`")

    def test_planted_italian_identifier_fails(self) -> None:
        path = self.write("web/a.ts", "export const totaleFattura = 1\n")
        self.assertEqual({w for _, w in self.words(path)}, {"totale", "fattura"})

    def test_copy_in_strings_and_jsx_is_exempt(self) -> None:
        path = self.write(
            "web/a.tsx",
            "export function Empty() {\n"
            "  const label = 'Nessuna fattura'\n"
            "  return <p title={`Cliente ${label}`}>Cliente non trovato, controlla la fattura</p>\n"
            "}\n",
        )
        self.assertEqual(self.lint(path), [])

    def test_italian_comment_fails_and_english_passes(self) -> None:
        bad = self.write(
            "web/b.ts", "// il totale della fattura non puo scendere\nexport const x = 1\n"
        )
        good = self.write(
            "web/c.ts", "// the total of the invoice cannot go down\nexport const y = 1\n"
        )
        self.assertIn("comment", {k for k, _ in self.words(bad)})
        self.assertEqual(self.lint(good), [])

    def test_jsx_attribute_and_property_names_are_identifiers(self) -> None:
        path = self.write("web/d.tsx", "export const x = <Row cliente={{ scadenza: 1 }} />\n")
        self.assertEqual({w for _, w in self.words(path)}, {"cliente", "scadenza"})


class CommandLine(Tree):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(HERE / "english_lint.py"), "--root", str(self.root), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_exit_status_follows_findings(self) -> None:
        self.write("ok.py", "total = 1\n")
        self.assertEqual(self.run_cli().returncode, 0)
        self.write("bad.py", "totale_fattura = 1\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("bad.py:1: [identifier]", result.stdout)


class Vocabulary(unittest.TestCase):
    def test_no_word_is_also_english(self) -> None:
        dictionary = Path("/usr/share/dict/american-english")
        if not dictionary.exists():
            self.skipTest("no system dictionary")
        english = {w.strip().lower() for w in dictionary.read_text(encoding="utf8").splitlines()}
        self.assertEqual(sorted(vocabulary.WORDS & english), [])
        for stem in vocabulary.STEMS:
            self.assertGreaterEqual(len(stem), 5, stem)
            self.assertEqual([w for w in english if w.startswith(stem)], [], stem)


if __name__ == "__main__":
    unittest.main()
