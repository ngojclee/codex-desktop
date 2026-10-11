#!/usr/bin/env python3
"""Regression coverage for packed payload integrity, independent of exe/header B2."""
import copy
import hashlib
import importlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from asar_integrity import repack_same_length_changes, scan_integrity
from patch_codex_asar_model_availability_filter import extract, make_header, read_header, walk


PATCHES = Path(__file__).resolve().parent


def integrity(data, block_size=16):
    return {
        "algorithm": "SHA256", "hash": hashlib.sha256(data).hexdigest(),
        "blockSize": block_size,
        "blocks": [hashlib.sha256(data[i:i + block_size]).hexdigest()
                   for i in range(0, max(len(data), 1), block_size)],
    }


def archive(files):
    header = {"files": {}}
    offset = 0
    for name, data in files.items():
        header["files"][name] = {
            "offset": str(offset), "size": len(data), "integrity": integrity(data),
        }
        offset += len(data)
    return make_header(header) + b"".join(files.values())


class AsarIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Path(self.temp.name)
        (self.app / "resources").mkdir()
        self.asar = self.app / "resources" / "app.asar"
        self.asar.write_bytes(archive({"chunk.js": b"var a=1;" * 8, "empty": b""}))

    def rewrite_header(self, edit):
        header, base = read_header(self.asar)
        payload = self.asar.read_bytes()[base:]
        edit(header)
        self.asar.write_bytes(make_header(header) + payload)

    def assert_bad(self, needle):
        result = scan_integrity(self.asar)
        self.assertGreater(result["mismatch_count"], 0, result)
        self.assertIn(needle, " ".join(result["mismatched"]))

    def invoke(self, script, *args):
        return subprocess.run(
            [sys.executable, str(PATCHES / script), *map(str, args)],
            capture_output=True, text=True, check=False,
        )

    def test_valid_multi_block_and_empty(self):
        result = scan_integrity(self.asar)
        self.assertEqual(result["checked"], 2)
        self.assertEqual(result["mismatch_count"], 0, result)

    def test_empty_block_hash_is_required(self):
        self.rewrite_header(lambda h: h["files"]["empty"]["integrity"].update(blocks=[]))
        self.assert_bad("block-count")

    def test_corrupt_content_fails(self):
        data = self.asar.read_bytes()
        self.asar.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        self.assert_bad("file-hash")
        self.assert_bad("block-hash")

    def test_correct_file_hash_stale_block_fails(self):
        self.rewrite_header(lambda h: h["files"]["chunk.js"]["integrity"]["blocks"].__setitem__(
            1, "0" * 64))
        self.assert_bad("block-hash")

    def test_correct_blocks_stale_file_hash_fails(self):
        self.rewrite_header(lambda h: h["files"]["chunk.js"]["integrity"].update(hash="0" * 64))
        self.assert_bad("file-hash")

    def test_truncated_payload_fails(self):
        self.asar.write_bytes(self.asar.read_bytes()[:-1])
        self.assert_bad("truncated payload")

    def test_truncated_header_fails(self):
        self.asar.write_bytes(b"\x04\x00")
        self.assert_bad("truncated ASAR prefix")

    def test_invalid_metadata_fails(self):
        for edit, needle in [
            (lambda m: m.update(size=-1), "negative size"),
            (lambda m: m.update(offset="not-an-offset"), "invalid literal"),
            (lambda m: m["integrity"].update(blockSize=0), "zero blockSize"),
            (lambda m: m["integrity"].update(blockSize=True), "invalid blockSize"),
            (lambda m: m["integrity"].update(algorithm="SHA1"), "algorithm"),
            (lambda m: m["integrity"].update(hash="bogus"), "digests"),
            (lambda m: m["integrity"].update(blocks=None), "digests"),
        ]:
            with self.subTest(needle=needle):
                self.asar.write_bytes(archive({"chunk.js": b"safe"}))
                self.rewrite_header(lambda h: edit(h["files"]["chunk.js"]))
                self.assert_bad(needle)

    def test_unpacked_link_and_no_integrity_are_explicit(self):
        def edit(header):
            header["files"]["chunk.js"].pop("integrity")
            header["files"]["external"] = {"unpacked": True, "size": 100}
            header["files"]["alias"] = {"link": "chunk.js"}
        self.rewrite_header(edit)
        result = scan_integrity(self.asar)
        self.assertEqual(result["mismatch_count"], 0, result)
        self.assertEqual(result["without_integrity"], 1)
        self.assertEqual(result["unpacked"], 1)

    def test_payload_gate_exits_nonzero(self):
        self.rewrite_header(lambda h: h["files"]["chunk.js"]["integrity"].update(hash="0" * 64))
        result = self.invoke("asar_integrity.py", self.asar)
        self.assertEqual(result.returncode, 1, result)
        self.assertIn("mismatched 1", result.stdout)

    def test_marker_verifier_rejects_before_markers(self):
        self.rewrite_header(lambda h: h["files"]["chunk.js"]["integrity"].update(hash="0" * 64))
        result = self.invoke("verify_markers.py", self.app)
        self.assertEqual(result.returncode, 1, result)
        self.assertIn("file-hash", result.stdout)
        self.assertNotIn("Could not find recent state", result.stderr)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell 7 not installed")
    def test_full_patch_runner_fails_on_stale_integrity(self):
        (self.app / "Codex.exe").write_bytes(b"fixture")
        self.rewrite_header(lambda h: h["files"]["chunk.js"]["integrity"].update(hash="0" * 64))
        skip = ["-SkipFuse"] + [f"-Skip{p}" for p in "A C D G M H J K L O P Q R S T U Y Z".split()]
        result = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(PATCHES.parent / "apply-all-patches.ps1"),
             "-AppDir", str(self.app), *skip],
            capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("file-hash", result.stdout)
        self.assertNotIn("All requested patches applied", result.stdout)

    def test_raw_patch_is_repacked_and_untouched_entry_stays_intact(self):
        original = self.asar.read_bytes()
        header, _ = read_header(self.asar)
        unchanged = copy.deepcopy(header["files"]["empty"])
        patched = original.replace(b"var a=1", b"var a=2")
        self.assertEqual(repack_same_length_changes(self.asar, original, patched), ["chunk.js"])
        self.assertEqual(scan_integrity(self.asar)["mismatch_count"], 0)
        new_header, _ = read_header(self.asar)
        self.assertEqual(new_header["files"]["empty"], unchanged)
        stable = self.asar.read_bytes()
        self.assertEqual(repack_same_length_changes(self.asar, stable, stable), [])
        self.assertEqual(self.asar.read_bytes(), stable)

    def test_repack_refuses_header_edit_or_length_change(self):
        original = self.asar.read_bytes()
        for patched in (original.replace(b"SHA256", b"SHA257", 1), original + b"x"):
            with self.subTest(length=len(patched)), self.assertRaises(RuntimeError):
                repack_same_length_changes(self.asar, original, patched)
            self.assertEqual(self.asar.read_bytes(), original)

    def test_stale_untouched_entry_is_not_silently_healed(self):
        self.rewrite_header(lambda h: h["files"]["empty"]["integrity"].update(hash="0" * 64))
        original = self.asar.read_bytes()
        repack_same_length_changes(self.asar, original, original.replace(b"var a=1", b"var a=2"))
        self.assert_bad("empty: file-hash")

    def test_all_integrity_writers_cover_empty_and_multi_block(self):
        writers = [
            "model_availability_filter", "autopaginate_v2", "autopaginate_v3",
            "recent_window", "reconnect_clear", "directive_windows_path",
            "ws_socks_bypass", "ws_max_payload",
        ]
        for name in writers:
            module = importlib.import_module(f"patch_codex_asar_{name}")
            for data in (b"", b"a" * 16, b"a" * 33):
                with self.subTest(writer=name, size=len(data)):
                    meta = {"integrity": integrity(b"old")}
                    module.update_integrity(meta, data)
                    self.assertEqual(meta, {"size": len(data), "integrity": integrity(data)})

    def assert_gate_roundtrip(self, script, files):
        self.asar.write_bytes(archive(files))
        before_header, _ = read_header(self.asar)
        untouched = copy.deepcopy(before_header["files"]["untouched"])
        result = self.invoke(script, "--app-dir", self.app, "--no-backup")
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["status"], "patched")
        self.assertGreater(len(output["changed_paths"]), 0)
        checked = scan_integrity(self.asar)
        self.assertEqual(checked["checked"], len(files))
        self.assertEqual(checked["mismatch_count"], 0, checked)
        header, base = read_header(self.asar)
        self.assertEqual(header["files"]["untouched"], untouched)
        for path, meta in walk(header):
            data = extract(self.asar, base, meta)
            if path not in output["changed_paths"]:
                self.assertEqual(data, files[path])
            self.assertEqual(meta["integrity"], integrity(data))
        stable = self.asar.read_bytes()
        result = self.invoke(script, "--app-dir", self.app, "--no-backup")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "already_patched")
        self.assertEqual(self.asar.read_bytes(), stable)

    def test_computer_use_gate_multi_file_multi_block(self):
        self.assert_gate_roundtrip("patch_codex_asar_computer_use_gate.py", {
            "cu.js": b"var a=c(`1506311413`);var b=bc(`410065390`);",
            "browser.js": b"var c=l(`410262010`);",
            "untouched": b"retain unchanged payload", "empty": b"",
        })

    def test_mobile_gate_keeps_archive_wide_modern_detection(self):
        self.assert_gate_roundtrip("patch_codex_asar_codex_mobile_gate.py", {
            "dialog.js": b"var a=c(`1042620455`);codexMobile.setupDialog.initial",
            "profile.js": b"codex-mobile-setup-dialog-abc.js;var b=c(`2798711298`);",
            "untouched": b"retain unchanged payload", "empty": b"",
        })

    def test_mobile_gate_legacy_layout(self):
        self.assert_gate_roundtrip("patch_codex_asar_codex_mobile_gate.py", {
            "legacy.js": (
                b"var a=c(`1042620455`);var b=c(`2798711298`);"
                b"function f({enabled:e,hasCompletedCodexMobileSetup:c,"
                b"remoteControlFeaturesVisible:v,remoteControlOnboardingEnabled:o})"
                b"{return e&&v&&o&&!c}"),
            "untouched": b"retain unchanged payload",
        })

    def test_production_asar_patchers_never_raw_write(self):
        for path in PATCHES.glob("patch_codex_asar_*.py"):
            with self.subTest(script=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotRegex(text, r"(?:asar|asar_path)\.write_bytes\(")
                self.assertTrue("repack(" in text or "repack_same_length_changes(" in text)

    def test_release_tag_targets_the_tested_checkout(self):
        workflow = (PATCHES.parent / ".github/workflows/auto-repatch-release.yml").read_text(
            encoding="utf-8")
        publish = workflow.split("gh release create $tag $asset", 1)[1].split(
            "\n        env:", 1)[0]
        self.assertIn("--target $env:GITHUB_SHA", publish)


if __name__ == "__main__":
    unittest.main()
