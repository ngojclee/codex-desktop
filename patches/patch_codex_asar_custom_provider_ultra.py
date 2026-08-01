#!/usr/bin/env python3
"""Patch S - expose catalog-declared Ultra effort for API-key providers.

Codex Desktop already understands `ultra`, and the Codex sidecar maps it to
provider-compatible `max` reasoning while enabling Ultra's app-level
orchestration behavior. The renderer still removes Ultra from model metadata
unless a ChatGPT Statsig gate is enabled, which also hides catalog-declared
Ultra entries from custom Responses-compatible providers.

This patch preserves the upstream Statsig gate for ChatGPT and other auth
methods. It only lets `apikey` hosts retain Ultra when the model catalog and
the built-in "Available reasoning efforts" setting both advertise it.
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


PATCH_MARKER = "/*S:apikey-ultra*/"
ULTRA_GATE_ID = "1186680773"
UPSTREAM_PATTERN = re.compile(
    r"(?P<result>[A-Za-z_$][A-Za-z0-9_$]*)="
    r"(?P<include>[A-Za-z_$][A-Za-z0-9_$]*)&&"
    r"(?P<getter>[A-Za-z_$][A-Za-z0-9_$]*)"
    rf"\((?P<gate_scope>[A-Za-z_$][A-Za-z0-9_$]*),`{ULTRA_GATE_ID}`\)"
)
AUTH_METHOD_PATTERN = re.compile(
    r"authMethod:(?P<auth>[A-Za-z_$][A-Za-z0-9_$]*)"
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


def _auth_method_var(text: str, match: re.Match):
    prefix = text[max(0, match.start() - 1200) : match.start()]
    candidates = list(AUTH_METHOD_PATTERN.finditer(prefix))
    if not candidates:
        raise RuntimeError("Could not infer authMethod variable near the Ultra gate")
    return candidates[-1].group("auth")


def patch_text(text: str):
    if PATCH_MARKER in text:
        return text, False

    matches = list(UPSTREAM_PATTERN.finditer(text))
    if len(matches) != 1:
        raise RuntimeError(
            "Expected exactly one model-list Ultra Statsig gate, "
            f"found {len(matches)}"
        )

    match = matches[0]
    auth = _auth_method_var(text, match)
    result = match.group("result")
    include = match.group("include")
    getter = match.group("getter")
    gate_scope = match.group("gate_scope")
    replacement = (
        f"{result}={include}&&({auth}===`apikey`||"
        f"{getter}({gate_scope},`{ULTRA_GATE_ID}`)){PATCH_MARKER}"
    )
    return text[: match.start()] + replacement + text[match.end() :], True


def syntax_errors(entries: list[tuple[str, str]]):
    node = shutil.which("node")
    if node is None:
        return ["node executable not found for Patch S syntax verification"]

    errors = []
    with tempfile.TemporaryDirectory(prefix="codex-patch-s-syntax-") as temp_dir:
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
        raise SystemExit("Verification failed: Patch S marker not found")
    if unpatched_paths:
        raise SystemExit(
            "Verification failed: upstream Ultra Statsig gates remain: "
            f"{sorted(set(unpatched_paths))}"
        )

    errors = syntax_errors(marker_entries)
    if errors:
        raise SystemExit(
            "Verification failed: Patch S syntax errors:\n"
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
        raise SystemExit("Could not find renderer model-list Ultra Statsig gate")

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
            backup = asar.with_name("app.asar.bak-before-custom-provider-ultra")
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
