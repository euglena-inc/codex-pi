"""Public bot attribution is distinct from personal email addresses."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "public_privacy", Path(__file__).resolve().parents[1] / "scripts/check_public_privacy.py")
privacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(privacy)


class PublicPrivacyTest(unittest.TestCase):
    def test_exact_public_bot_attribution_is_allowed(self):
        self.assertEqual(privacy.inspect("commit", b"Co-authored-by: tool <noreply@anthropic.com>"), [])
        self.assertEqual(privacy.inspect("commit", b"NOREPLY@ANTHROPIC.COM"), [])

    def test_other_addresses_at_that_domain_are_rejected(self):
        for local in (b"example-person", b"noreply+other", b"x-noreply"):
            with self.subTest(local=local):
                value = local + b"@" + b"anthropic.com"
                self.assertTrue(any("email address" in finding
                                    for finding in privacy.inspect("commit", value)))

    def test_bot_prefix_does_not_allow_other_domains(self):
        value = b"noreply" + b"@" + b"example.net"
        self.assertTrue(any("email address" in finding
                            for finding in privacy.inspect("commit", value)))
