#!/usr/bin/env python3
"""Patch T - remove the Ctrl+Shift+V default binding from Voice Mode.

On Windows, Ctrl+Shift+V is the conventional paste-as-plain-text shortcut.
Current Codex Desktop bundles also register it as the default keybinding for
`composer.startVoiceMode`. That command collision can make a single keyboard
gesture travel through both the command system and the editor paste path.

This patch removes only the default Voice Mode binding. Voice Mode remains
available from the UI and can still be assigned a different custom shortcut.
Ordinary Ctrl+V behavior is intentionally left unchanged.
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
    read_header,
    repack,
    walk,
)


PATCH_MARKER = "/*T:no-voice-paste-binding*/"
UPSTREAM_PATTERN = re.compile(
    r"(?P<prefix>\{(?:\"id\"|id):(?P<quote>[`'\"])"
    r"composer\.startVoiceMode(?P=quote),"
    r"[^{}]{0,1600}?(?:\"electron\"|electron):\{)"
    r"(?:\"defaultKeybindings\"|defaultKeybindings):"
    r"\[\{(?:\"key\"|key):(?P=quote)Ctrl\+Shift\+V(?P=quote)\}\]"
    r"(?P<suffix>\}\})"
)

# 26.825 moved Voice Mode to per-platform defaults and left the non-macOS
# bucket empty (`platformDefaultKeybindings:{macOS:[{key:`Ctrl+Shift+V`}],
# default:[]}`), which is exactly the outcome Patch T was created to produce
# on Windows. Treat that layout as already safe instead of failing.
UPSTREAM_SAFE_PATTERN = re.compile(
    r"(?:\"id\"|id):(?P<quote>[`'\"])composer\.startVoiceMode(?P=quote),"
    r"[^{}]{0,1600}?(?:\"electron\"|electron):\{"
    r"(?:\"platformDefaultKeybindings\"|platformDefaultKeybindings):\{"
    r"(?:[^{}]|\[[^][]*\{[^{}]*\}[^][]*\])*?"
    r"(?:\"default\"|default):\s*\[\]"
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
        if (
            PATCH_MARKER in text
            or UPSTREAM_PATTERN.search(text)
            or UPSTREAM_SAFE_PATTERN.search(text)
        ):
            targets.append((path, meta, text))
    return header, payload_start, targets


def patch_text(text: str):
    if PATCH_MARKER in text:
        return text, False

    matches = list(UPSTREAM_PATTERN.finditer(text))
    if len(matches) != 1:
        raise RuntimeError(
            "Expected exactly one Ctrl+Shift+V Voice Mode default binding, "
            f"found {len(matches)}"
        )

    match = matches[0]
    replacement = (
        f"{match.group('prefix')}defaultKeybindings:[]{PATCH_MARKER}"
        f"{match.group('suffix')}"
    )
    return text[: match.start()] + replacement + text[match.end() :], True


def syntax_errors(entries: list[tuple[str, str]]):
    node = shutil.which("node")
    if node is None:
        return ["node executable not found for Patch T syntax verification"]

    errors = []
    with tempfile.TemporaryDirectory(prefix="codex-patch-t-syntax-") as temp_dir:
        for index, (path, text) in enumerate(entries):
            check_path = Path(temp_dir) / f"chunk-{index}.mjs"
            check_path.write_text(text, encoding="utf-8")
            result = subprocess.run(
                [node, "--check", str(check_path)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip().splitlines()
                errors.append(
                    f"{path}: {detail[-1] if detail else 'node --check failed'}"
                )
    return errors


def verify(asar: Path):
    _header, _payload_start, targets = find_targets(asar)
    marker_entries = [
        (path, text) for path, _meta, text in targets if PATCH_MARKER in text
    ]
    safe_entries = [
        (path, text)
        for path, _meta, text in targets
        if UPSTREAM_SAFE_PATTERN.search(text)
    ]
    unpatched_paths = [
        path for path, _meta, text in targets if UPSTREAM_PATTERN.search(text)
    ]
    if not marker_entries and not safe_entries:
        raise SystemExit(
            "Verification failed: Patch T marker not found and upstream does "
            "not scope the Voice Mode Ctrl+Shift+V binding away from Windows"
        )
    if unpatched_paths:
        raise SystemExit(
            "Verification failed: Ctrl+Shift+V Voice Mode binding remains in "
            f"{sorted(set(unpatched_paths))}"
        )

    errors = syntax_errors(marker_entries + safe_entries)
    if errors:
        raise SystemExit(
            "Verification failed: Patch T syntax errors:\n"
            + "\n".join(f"  - {error}" for error in errors)
        )
    return {
        "marker_paths": sorted(path for path, _text in marker_entries),
        "upstream_safe_paths": sorted(path for path, _text in safe_entries),
        "unpatched_paths": sorted(set(unpatched_paths)),
        "syntax_errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--app-dir", required=True, help="Codex install dir (contains resources/app.asar)"
    )
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    app_dir = Path(args.app_dir).resolve()
    asar = app_dir / "resources" / "app.asar"
    if not asar.exists():
        raise SystemExit(f"Missing ASAR: {asar}")

    header, payload_start, targets = find_targets(asar)
    if not targets:
        raise SystemExit("Could not find the Voice Mode shortcut command in app.asar")

    patched_by_path = {}
    scanned = []
    for path, _meta, text in targets:
        scanned.append(path)
        if PATCH_MARKER not in text and UPSTREAM_SAFE_PATTERN.search(text):
            # Upstream already keeps Ctrl+Shift+V off Windows; nothing to do.
            continue
        try:
            patched_text, changed = patch_text(text)
        except RuntimeError as exc:
            raise SystemExit(f"{path}: {exc}") from exc
        if changed:
            patched_by_path[path] = patched_text.encode("utf-8")

    if patched_by_path:
        if not args.no_backup:
            backup = asar.with_name("app.asar.bak-before-voice-paste-shortcut")
            if not backup.exists():
                shutil.copy2(asar, backup)
        repack(asar, header, payload_start, patched_by_path)

    result = verify(asar)
    if patched_by_path:
        status = "patched"
    elif result["marker_paths"]:
        status = "already_patched"
    else:
        status = "upstream_safe"
    print(
        json.dumps(
            {
                "status": status,
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
