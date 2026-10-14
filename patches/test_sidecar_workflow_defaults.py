"""Execute the workflow's version-stamp block with real PowerShell fixtures."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/auto-repatch-release.yml"


class SidecarWorkflowDefaultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.shell = shutil.which("pwsh")
        if not cls.shell:
            raise unittest.SkipTest("PowerShell 7 is required")
        cls.pin = re.search(r"^  VERIFIED_CODEX_REF: (\S+)$", cls.workflow, re.M)[1]
        cls.version = re.search(
            r"^  VERIFIED_SIDECAR_VERSION: '([^']+)'$", cls.workflow, re.M
        )[1]
        start = cls.workflow.index("          $workspaceManifest =")
        end = cls.workflow.index("          Write-Host 'Rust/Cargo versions:'", start)
        cls.block = "\n".join(line[10:] for line in cls.workflow[start:end].splitlines())

    def stamp(self, source="0.0.0", requested="", ref=None, newline="\n"):
        with tempfile.TemporaryDirectory(prefix="codex-workflow-stamp-") as scratch:
            manifest = Path(scratch) / "Cargo.toml"
            original = newline.join(
                ['[workspace.package]', f'version = "{source}"', "",
                 "[dependencies]", 'other = { version = "9.9.9" }', ""]
            )
            manifest.write_bytes(original.encode("utf-8"))
            block = self.block.replace("${{ inputs.sidecar_version }}", requested)
            block = block.replace("${{ env.VERIFIED_CODEX_REF }}", self.pin)
            block = block.replace("${{ env.VERIFIED_SIDECAR_VERSION }}", self.version)
            command = (
                "$ErrorActionPreference = 'Stop'\n"
                f"$codexRef = '{ref or self.pin}'\n" + block
            )
            result = subprocess.run(
                [self.shell, "-NoProfile", "-NonInteractive", "-Command", command],
                cwd=scratch, capture_output=True, text=True, encoding="utf-8",
            )
            return result, manifest.read_bytes(), original.encode("utf-8")

    def test_schedule_uses_verified_version_for_pinned_source(self):
        result, data, _ = self.stamp()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'version = "{self.version}"'.encode(), data)
        self.assertIn(b'other = { version = "9.9.9" }', data)
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"))

    def test_dispatch_override_is_preserved(self):
        result, data, _ = self.stamp(requested="0.155.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'version = "0.155.0"', data)

    def test_unverified_ref_requires_explicit_version(self):
        result, data, original = self.stamp(ref="unverified-source")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no sidecar_version was supplied", result.stderr)
        self.assertEqual(data, original)

    def test_real_source_version_is_unchanged(self):
        result, data, original = self.stamp(source="0.162.1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data, original)

    def test_below_renderer_floor_is_rejected(self):
        result, data, original = self.stamp(requested="0.145.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("below the Desktop fork floor", result.stderr)
        self.assertEqual(data, original)

    def test_crlf_manifest_is_preserved(self):
        result, data, _ = self.stamp(newline="\r\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotRegex(data, rb"(?<!\r)\n")

    def test_x_filters_are_separate_cargo_invocations(self):
        start = self.workflow.index("          Write-Host 'Running Patch X")
        end = self.workflow.index("          Write-Host 'Running Patch W1", start)
        block = "\n".join(line[10:] for line in self.workflow[start:end].splitlines())
        command = (
            "$ErrorActionPreference = 'Stop'\n"
            "$script:calls = @()\n"
            "function cargo { $script:calls += ,@($args); $global:LASTEXITCODE = 0 }\n"
            + block + "\nConvertTo-Json -InputObject $script:calls -Compress\n"
        )
        result = subprocess.run(
            [self.shell, "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(
            calls,
            [["test", "-p", "codex-core", "--lib", f"client::tests::{name}"]
             for name in ("inherited", "binary", "custom")],
        )


if __name__ == "__main__":
    unittest.main()
