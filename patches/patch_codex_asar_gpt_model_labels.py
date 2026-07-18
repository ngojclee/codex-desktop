#!/usr/bin/env python3
"""Patch Q - preserve GPT prefixes in renderer model labels.

Some catalog entries use display names such as `GPT-5.5`, while others use
`GPT 5.4`. The renderer strips a leading `GPT-` in several model-picker paths,
which makes affected entries appear as only `5.5`, `5.4-Mini`, or
`5.3 Codex Spark`.

This patch normalizes a leading `GPT-` (case-insensitive) to `GPT ` instead of
removing it. Labels that already use `GPT ` remain unchanged.
"""
import argparse
import json
import re
import shutil
from pathlib import Path

from patch_codex_asar_model_availability_filter import (
    extract,
    read_header,
    repack,
    walk,
)


PATCH_MARKER = "/*Q:gpt-label*/"
OLD_PATTERN = re.compile(r"\.replace\(/\^GPT-/iu,(?:``|\"\"|'')\)")
REPLACEMENT = ".replace(/^GPT-/iu,`GPT `)" + PATCH_MARKER


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
        if OLD_PATTERN.search(text) or PATCH_MARKER in text:
            targets.append((path, meta, text))
    return header, payload_start, targets


def verify(asar: Path):
    _header, _payload_start, targets = find_targets(asar)
    marker_paths = []
    old_paths = []
    marker_count = 0
    old_count = 0

    for path, _meta, text in targets:
        markers = text.count(PATCH_MARKER)
        old_calls = len(OLD_PATTERN.findall(text))
        marker_count += markers
        old_count += old_calls
        if markers:
            marker_paths.append(path)
        if old_calls:
            old_paths.append(path)

    if old_paths:
        raise SystemExit(
            f"Verification failed: GPT prefix stripping remains in {sorted(set(old_paths))}"
        )
    if not marker_paths:
        raise SystemExit("Verification failed: Patch Q GPT label marker not found")

    return {
        "marker_paths": sorted(set(marker_paths)),
        "marker_count": marker_count,
        "old_paths": sorted(set(old_paths)),
        "old_count": old_count,
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
        raise SystemExit("Could not find renderer model label target in app.asar")

    patched_by_path = {}
    scanned = []
    replacement_count = 0
    for path, _meta, text in targets:
        scanned.append(path)
        patched_text, count = OLD_PATTERN.subn(REPLACEMENT, text)
        replacement_count += count
        if count:
            patched_by_path[path] = patched_text.encode("utf-8")
        elif PATCH_MARKER not in text:
            raise SystemExit(
                f"{path}: model label layout found but GPT prefix strip calls did not match"
            )

    if patched_by_path:
        if not args.no_backup:
            backup = asar.with_name("app.asar.bak-before-gpt-model-labels")
            if not backup.exists():
                shutil.copy2(asar, backup)
        repack(asar, header, payload_start, patched_by_path)

    result = verify(asar)
    print(
        json.dumps(
            {
                "status": "patched" if patched_by_path else "already_patched",
                "asar": str(asar),
                "scanned": sorted(set(scanned)),
                "patched": sorted(patched_by_path),
                "replacements": replacement_count,
                **result,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
