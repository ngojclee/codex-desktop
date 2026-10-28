import unittest

from verify_public_source import findings


class PublicSourceTests(unittest.TestCase):
    def test_private_networks_are_rejected(self):
        for address in (b"10." + b"0.0.7", b"192." + b"168.1.8", b"172." + b"16.0.9"):
            with self.subTest(address_family=address.split(b".", 1)[0]):
                self.assertIn("private_ipv4", findings("README.md", address))

    def test_documentation_addresses_and_dynamic_paths_are_allowed(self):
        data = b"http://192.0.2.10/mcp http://127.0.0.1/mcp $env:USERPROFILE <HOME> <PROJECTS>"
        self.assertEqual([], findings("README.md", data))

    def test_literal_machine_paths_and_hostnames_are_rejected(self):
        fixtures = {
            "literal_user_profile": b"C:" + b"\\Users\\" + b"example\\work",
            "literal_private_checkout": b"D:" + b"\\Python\\" + b"projects",
            "machine_hostname": b"DESKTOP-" + b"EXAMPLE",
        }
        for label, data in fixtures.items():
            with self.subTest(category=label):
                self.assertIn(label, findings("README.md", data))

    def test_private_files_are_rejected_even_without_secret_shapes(self):
        for path in ("HANDOFF_DESK.md", "docs/HANDOFF.md", "DEV_HANDOFF.md", ".env",
                     "auth.json", "config.toml", "state.sqlite", "session.jsonl"):
            with self.subTest(path=path):
                self.assertTrue(findings(path, b"innocent-looking text"))

    def test_public_examples_and_release_docs_are_allowed(self):
        for path in (".env.example", "docs/PUBLIC_RELEASES.md", "docs/RELEASE_RUNBOOK.md"):
            self.assertEqual([], findings(path, b"PORT=8080"))

    def test_credential_shape_is_rejected_without_echoing_value(self):
        self.assertIn("credential_shape", findings("README.md", b"ghp_" + b"A" * 36))


if __name__ == "__main__":
    unittest.main()
