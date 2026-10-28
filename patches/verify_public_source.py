"""Check tracked source for private machine data without printing matched values."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path, PurePosixPath


CONTENT_RULES = {
    "private_ipv4": re.compile(
        rb"(?<![\w.])(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
        rb"192\.168\.\d{1,3}\.\d{1,3}|"
        rb"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?![\w.])"
    ),
    "literal_user_profile": re.compile(
        rb"(?i)\b[A-Z]:[\\/]+Users[\\/]+(?!Public\b|Default\b)[A-Z0-9_.-]+"
    ),
    "literal_private_checkout": re.compile(
        rb"(?i)\b[A-Z]:[\\/]+Python[\\/]+projects\b"
    ),
    "machine_hostname": re.compile(rb"(?<![\w-])DESKTOP-[A-Z0-9]{7}(?![\w-])"),
    "credential_shape": re.compile(
        rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|"
        rb"sk-(?:proj-)?[A-Za-z0-9_-]{32,}|"
        rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
    ),
}
PRIVATE_DOCS = {
    "dev_handoff.md",
    "2026-07-10-codex-patched-update-handoff.md",
    "codex-relay-dev-handoff.md",
}


def findings(path: str, data: bytes) -> list[str]:
    name = PurePosixPath(path.replace("\\", "/")).name.lower()
    result = []
    if name in PRIVATE_DOCS or (name.startswith("handoff") and name.endswith(".md")):
        result.append("private_handoff")
    if (
        (name.startswith(".env") and not name.endswith(".example"))
        or name in {"auth.json", "config.toml"}
        or name.startswith("mcp-secrets")
        or name.endswith((".sqlite", ".dmp", ".log", ".jsonl"))
    ):
        result.append("private_state_file")
    result.extend(label for label, pattern in CONTENT_RULES.items() if pattern.search(data))
    return result


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).split(b"\0")
    checked = 0
    failed = 0
    for encoded_path in paths:
        if not encoded_path:
            continue
        path = encoded_path.decode("utf-8")
        source = root / path
        if not source.is_file():
            continue
        checked += 1
        labels = findings(path, source.read_bytes())
        if labels:
            failed += 1
            print(f"FAIL: {path}: {', '.join(labels)}")
    print(f"Public source hygiene: {checked} tracked files, {failed} flagged files.")
    return int(failed != 0)


if __name__ == "__main__":
    sys.exit(main())
