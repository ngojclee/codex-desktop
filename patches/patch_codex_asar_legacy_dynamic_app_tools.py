#!/usr/bin/env python3
"""Patch Z - keep legacy dynamic app-tool calls working for old threads.

Codex Desktop moved thread/automation tools from the old dynamic app-tool path
to the `codex_app` MCP server. The renderer now deliberately rejects old calls
such as `automation_update` from threads that still carry the old tool names.

New threads use MCP. Existing conversations keep the old tool names in their
history, so this patch relaxes only that compatibility guard for the five tools
in the legacy allow-list. The replacement preserves byte length; CI verifies the
patched guard directly because the anchor has no spare bytes for a comment.
"""
import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from patch_codex_asar_model_availability_filter import (
    extract,
    node_failure_line,
    read_header,
    repack,
    walk,
)


IDENT = r"[A-Za-z_$][A-Za-z0-9_$]*"

UPSTREAM_PATTERN = re.compile(
    rf"(?P<var>{IDENT})===`dynamic`&&\("
    r"(?P<middle>[\s\S]{0,1200}?)"
    rf"\)&&\((?P<gate>{IDENT})\((?P<host>{IDENT})\)\|\|"
    rf"(?P<legacy>{IDENT})\.has\((?P<tool>{IDENT})\)\)"
)
PATCHED_PATTERN = re.compile(
    rf"(?P<var>{IDENT})===`dynamic`&&\("
    r"(?P<middle>[\s\S]{0,1200}?)"
    rf"\)&&\((?P<gate>{IDENT})\((?P<host>{IDENT})\)&!"
    rf"(?P<legacy>{IDENT})\.has\((?P<tool>{IDENT})\)\)"
)


def relevant_match(match: re.Match) -> bool:
    middle = match.group("middle")
    return "`plugin_management`" in middle and "`openai_settings`" in middle


def has_relevant_pattern(text: str):
    return any(
        relevant_match(m)
        for m in UPSTREAM_PATTERN.finditer(text)
    ) or any(
        relevant_match(m)
        for m in PATCHED_PATTERN.finditer(text)
    )


def find_targets(asar: Path):
    header, payload_start = read_header(asar)
    targets = []
    for path, meta in walk(header):
        if not (
            path.startswith("webview/assets/")
            and path.endswith(".js")
            and "offset" in meta
        ):
            continue
        text = extract(asar, payload_start, meta).decode("utf-8", "replace")
        if has_relevant_pattern(text):
            targets.append((path, meta, text))
    return header, payload_start, targets


def patch_text(text: str):
    has_patched = any(relevant_match(m) for m in PATCHED_PATTERN.finditer(text))
    has_upstream = any(relevant_match(m) for m in UPSTREAM_PATTERN.finditer(text))
    if has_patched:
        if not has_upstream:
            return text, False
        raise RuntimeError("Patch Z guard is incomplete or conflicts with upstream guard")

    matches = [m for m in UPSTREAM_PATTERN.finditer(text) if relevant_match(m)]
    if len(matches) != 1:
        raise RuntimeError(
            "Expected exactly one legacy dynamic app-tool guard, "
            f"found {len(matches)}"
        )

    match = matches[0]
    replacement = (
        f"{match.group('var')}===`dynamic`&&({match.group('middle')})&&("
        f"{match.group('gate')}({match.group('host')})&!"
        f"{match.group('legacy')}.has({match.group('tool')}))"
    )
    if len(replacement) != len(match.group(0)):
        raise RuntimeError("Patch Z replacement must be byte-length preserving")
    return text[: match.start()] + replacement + text[match.end() :], True


def syntax_errors(entries: list[tuple[str, str]]):
    node = shutil.which("node")
    if node is None:
        return ["node executable not found for Patch Z syntax verification"]

    errors = []
    with tempfile.TemporaryDirectory(prefix="codex-patch-z-syntax-") as temp_dir:
        for index, (path, text) in enumerate(entries):
            check_path = Path(temp_dir) / f"chunk-{index}.mjs"
            check_path.write_text(text, encoding="utf-8")
            result = subprocess.run(
                [node, "--check", str(check_path)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if result.returncode != 0:
                errors.append(f"{path}: {node_failure_line(result.stderr or result.stdout)}")
    return errors


def status(asar: Path):
    _header, _payload_start, targets = find_targets(asar)
    patched_entries = [(path, text) for path, _meta, text in targets if any(
        relevant_match(m) for m in PATCHED_PATTERN.finditer(text)
    )]
    unpatched_paths = [
        path
        for path, _meta, text in targets
        if any(relevant_match(m) for m in UPSTREAM_PATTERN.finditer(text))
    ]
    bad_marker_paths = [
        path
        for path, _meta, text in targets
        if not any(relevant_match(m) for m in PATCHED_PATTERN.finditer(text))
    ]
    if not patched_entries:
        raise SystemExit("Verification failed: Patch Z guard not found")
    if bad_marker_paths:
        raise SystemExit(
            "Verification failed: expected Patch Z guard not present: "
            f"{sorted(set(bad_marker_paths))}"
        )
    if unpatched_paths:
        raise SystemExit(
            "Verification failed: legacy dynamic app-tool guard remains: "
            f"{sorted(set(unpatched_paths))}"
        )
    errors = syntax_errors(patched_entries)
    if errors:
        raise SystemExit(
            "Verification failed: Patch Z syntax errors:\n"
            + "\n".join(f"  - {error}" for error in errors)
        )
    return {
        "marker_paths": sorted(path for path, _text in patched_entries),
        "unpatched_paths": sorted(set(unpatched_paths)),
        "syntax_errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--app-dir", required=True)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    app_dir = Path(args.app_dir).resolve()
    asar = app_dir / "resources" / "app.asar"
    if not asar.exists():
        raise SystemExit(f"Missing ASAR: {asar}")

    header, payload_start, targets = find_targets(asar)
    if not targets:
        raise SystemExit("Could not find the legacy dynamic app-tool guard in app.asar")

    patched_by_path = {}
    scanned = []
    for path, _meta, text in targets:
        scanned.append(path)
        try:
            patched_text, changed = patch_text(text)
        except RuntimeError as exc:
            raise SystemExit(f"{path}: {exc}") from exc
        if changed:
            patched_by_path[path] = patched_text.encode("utf-8")

    if patched_by_path:
        if not args.no_backup:
            backup = asar.with_name("app.asar.bak-before-legacy-dynamic-app-tools")
            if not backup.exists():
                shutil.copy2(asar, backup)
        repack(asar, header, payload_start, patched_by_path)

    result = status(asar)
    print(
        json.dumps(
            {
                "status": "patched" if patched_by_path else "already_patched",
                "asar": str(asar),
                "scanned": sorted(set(scanned)),
                "patched": sorted(patched_by_path),
                **result,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
