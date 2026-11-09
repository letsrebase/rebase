"""The DNS name check (REB-914): the committed zone passes, an Italian host name fails, and
the allowances are the ones the decision row names."""

import unittest
from pathlib import Path

import check_dns_names as dns

TEMPLATE = """resource "cloudflare_dns_record" "rebase_x" {
  provider = cloudflare.rebase
  name     = "%s"
  type     = "A"
}
"""


def zone(*names: str) -> str:
    return "\n".join(TEMPLATE % name for name in names)


class DnsNames(unittest.TestCase):
    def test_the_committed_zone_passes(self) -> None:
        text = dns.DEFAULT_FILE.read_text(encoding="utf-8")
        self.assertGreater(len(dns.record_names(text)), 10)
        self.assertEqual(dns.check(text), [])

    def test_a_planted_italian_host_name_fails(self) -> None:
        findings = dns.check(zone("prova.letsrebase.com"))
        self.assertEqual(len(findings), 1)
        self.assertIn("prova", findings[0])

    def test_every_word_of_a_compound_name_is_read(self) -> None:
        self.assertEqual(len(dns.check(zone("preview.prova-cliente.letsrebase.com"))), 2)

    def test_english_names_and_proper_nouns_pass(self) -> None:
        names = (
            "letsrebase.com",
            "www.letsrebase.com",
            "_dmarc.letsrebase.com",
            "pigrocrm.letsrebase.com",
            "preview.pigrocrm.letsrebase.com",
            "sign.letsrebase.com",
        )
        self.assertEqual(dns.check(zone(*names)), [])

    def test_the_two_hosts_that_are_moving_are_allowed_until_retired(self) -> None:
        self.assertEqual(
            dns.check(zone("firma.letsrebase.com", "preview.pigro.letsrebase.com")), []
        )
        self.assertEqual(dns.ALLOWED_WORDS, {"pigrocrm", "firma", "pigro"})

    def test_only_dns_records_are_read(self) -> None:
        text = 'resource "cloudflare_zone" "z" {\n  name = "prova.example"\n}\n'
        self.assertEqual(dns.check(text), [])

    def test_the_script_exits_non_zero_on_a_planted_file(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            planted = Path(tmp) / "rebase.tf"
            planted.write_text(zone("prova.letsrebase.com"), encoding="utf-8")
            self.assertEqual(dns.main(["check_dns_names.py", str(planted)]), 1)
            self.assertEqual(dns.main(["check_dns_names.py", str(dns.DEFAULT_FILE)]), 0)


if __name__ == "__main__":
    unittest.main()
