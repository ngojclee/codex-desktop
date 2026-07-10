#!/usr/bin/env python3
"""Patch R - expose catalog-declared Fast service tier controls for API providers.

The current Desktop renderer only shows service-tier controls when the host is
authenticated with ChatGPT. That is correct for OpenAI Fast credits, but it
also hides the Standard/Fast selector for custom Responses-compatible
providers that intentionally expose a Fast tier in their local model catalog.

This patch preserves the upstream ChatGPT entitlement check. For non-ChatGPT
providers it allows the existing model catalog to decide whether any service
tier options exist. It does not set `service_tier` in config, grant OpenAI
Fast credits, or add Fast to a model that did not already advertise it.
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


PATCH_MARKER = "/*R:custom-provider-fast*/"
UPSTREAM_PATTERN = re.compile(
    r"(?P<allowed>[A-Za-z_$][A-Za-z0-9_$]*)="
    r"(?P<chatgpt>[A-Za-z_$][A-Za-z0-9_$]*)&&"
    r"!(?P<loading>[A-Za-z_$][A-Za-z0-9_$]*)&&"
    r"(?P<requirements>[A-Za-z_$][A-Za-z0-9_$]*)!=null&&"
    r"(?P=requirements)\?\.requirements\?\.featureRequirements\?\.fast_mode!==!1"
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
        if PATCH_MARKER in text or UPSTREAM_PATTERN.search(text):
            targets.append((path, meta, text))
    return header, payload_start, targets


def patch_text(text: str):
    if PATCH_MARKER in text:
        return text, False

    matches = list(UPSTREAM_PATTERN.finditer(text))
    if len(matches) != 1:
        raise RuntimeError(
            "Expected exactly one Fast service-tier auth gate, "
            f"found {len(matches)}"
        )

    match = matches[0]
    allowed = match.group("allowed")
    chatgpt = match.group("chatgpt")
    loading = match.group("loading")
    requirements = match.group("requirements")
    replacement = (
        f"{allowed}=!{loading}&&(!{chatgpt}||{requirements}!=null&&"
        f"{requirements}?.requirements?.featureRequirements?.fast_mode!==!1)"
        f"{PATCH_MARKER}"
    )
    return text[: match.start()] + replacement + text[match.end() :], True


def syntax_errors(entries: list[tuple[str, str]]):
    node = shutil.which("node")
    if node is None:
        return ["node executable not found for Patch R syntax verification"]

    errors = []
    with tempfile.TemporaryDirectory(prefix="codex-patch-r-syntax-") as temp_dir:
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
    unpatched_paths = [
        path for path, _meta, text in targets if UPSTREAM_PATTERN.search(text)
    ]
    if not marker_entries:
        raise SystemExit("Verification failed: Patch R marker not found")
    if unpatched_paths:
        raise SystemExit(
            "Verification failed: upstream Fast auth gates remain: "
            f"{sorted(set(unpatched_paths))}"
        )

    errors = syntax_errors(marker_entries)
    if errors:
        raise SystemExit(
            "Verification failed: Patch R syntax errors:\n"
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
        raise SystemExit("Could not find renderer Fast service-tier auth gate")

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
            backup = asar.with_name("app.asar.bak-before-custom-provider-fast-mode")
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
