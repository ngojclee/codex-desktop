#!/usr/bin/env python3
"""Patch Y - keep automation_update mode validation compatible with embedded Zod.

The renderer schema contains two outer discriminated unions whose members
include nested discriminated unions.  Some Desktop bundles ship an embedded
Zod build that collects the nested enum values incorrectly, so valid
``create`` and ``update`` requests can fail before they reach the app MCP.

Keep the inner kind unions and all refinements intact.  Only replace the two
outer mode unions with ordinary chained unions, which preserve validation for
valid inputs without asking the embedded discriminator-map builder to flatten
nested members.
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


PATCH_MARKER = "/*Y:automation-mode-union*/"
IDENT = r"[A-Za-z_$][A-Za-z0-9_$]*"

# Keep the member order and the two different inner-union names explicit.
# These are the schema's stable semantic roles; a drifted minified layout must
# stop the release rather than silently ship without the fix.
Y_MODE_PATTERN = re.compile(
    rf"(?P<name>{IDENT})=zh\(`mode`,\[sWn,_Wn,vWn,cWn\]\)"
)
X_MODE_PATTERN = re.compile(
    rf"(?P<name>{IDENT})=zh\(`mode`,\[sWn,bWn,vWn,cWn\]\)"
)
PATCHED_Y_PATTERN = re.compile(
    rf"(?P<name>{IDENT})=sWn\.or\(_Wn\)\.or\(vWn\)\.or\(cWn\){re.escape(PATCH_MARKER)}"
)
PATCHED_X_PATTERN = re.compile(
    rf"(?P<name>{IDENT})=sWn\.or\(bWn\)\.or\(vWn\)\.or\(cWn\){re.escape(PATCH_MARKER)}"
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
            or Y_MODE_PATTERN.search(text)
            or X_MODE_PATTERN.search(text)
        ):
            targets.append((path, meta, text))
    return header, payload_start, targets


def patch_text(text: str):
    marker_count = text.count(PATCH_MARKER)
    if marker_count:
        if marker_count != 2 or not (
            PATCHED_Y_PATTERN.search(text) and PATCHED_X_PATTERN.search(text)
        ):
            raise RuntimeError(
                "Patch Y marker is partial or malformed; refusing to continue"
            )
        if Y_MODE_PATTERN.search(text) or X_MODE_PATTERN.search(text):
            raise RuntimeError(
                "Patch Y has both patched and upstream automation unions"
            )
        return text, False

    y_matches = list(Y_MODE_PATTERN.finditer(text))
    x_matches = list(X_MODE_PATTERN.finditer(text))
    if len(y_matches) != 1:
        raise RuntimeError(
            "Expected exactly one automation validation union with _Wn, "
            f"found {len(y_matches)}"
        )
    if len(x_matches) != 1:
        raise RuntimeError(
            "Expected exactly one automation validation union with bWn, "
            f"found {len(x_matches)}"
        )

    # Apply from right to left so the first match offset remains valid.
    replacements = [
        (
            y_matches[0].start(),
            y_matches[0].end(),
            f"{y_matches[0].group('name')}=sWn.or(_Wn).or(vWn).or(cWn)"
            + PATCH_MARKER,
        ),
        (
            x_matches[0].start(),
            x_matches[0].end(),
            f"{x_matches[0].group('name')}=sWn.or(bWn).or(vWn).or(cWn)"
            + PATCH_MARKER,
        ),
    ]
    patched = text
    for start, end, replacement in sorted(replacements, reverse=True):
        patched = patched[:start] + replacement + patched[end:]
    return patched, True


def syntax_errors(entries: list[tuple[str, str]]):
    node = shutil.which("node")
    if node is None:
        return ["node executable not found for Patch Y syntax verification"]

    errors = []
    with tempfile.TemporaryDirectory(prefix="codex-patch-y-syntax-") as temp_dir:
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


def verify(asar: Path):
    _header, _payload_start, targets = find_targets(asar)
    marker_entries = [
        (path, text)
        for path, _meta, text in targets
        if PATCH_MARKER in text
    ]
    unpatched_paths = [
        path
        for path, _meta, text in targets
        if Y_MODE_PATTERN.search(text) or X_MODE_PATTERN.search(text)
    ]
    if not marker_entries:
        raise SystemExit("Verification failed: Patch Y marker not found")
    if len(marker_entries) != 1:
        raise SystemExit(
            "Verification failed: expected Patch Y in one renderer chunk, "
            f"found {len(marker_entries)}"
        )
    if unpatched_paths:
        raise SystemExit(
            "Verification failed: upstream automation mode unions remain: "
            f"{sorted(set(unpatched_paths))}"
        )

    marker_text = marker_entries[0][1]
    if marker_text.count(PATCH_MARKER) != 2:
        raise SystemExit("Verification failed: Patch Y must replace both mode unions")
    errors = syntax_errors(marker_entries)
    if errors:
        raise SystemExit(
            "Verification failed: Patch Y syntax errors:\n"
            + "\n".join(f"  - {error}" for error in errors)
        )
    return {
        "marker_paths": sorted(path for path, _text in marker_entries),
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
        raise SystemExit("Could not find renderer automation mode validation unions")

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
            backup = asar.with_name("app.asar.bak-before-automation-mode-union")
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
                **result,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
