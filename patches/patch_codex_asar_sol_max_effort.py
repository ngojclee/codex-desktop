#!/usr/bin/env python3
"""Patch P - add Max to the compact Work power slider for gpt-5.6-sol.

The model catalog and the built-in "Available reasoning efforts" setting own
whether `max` is enabled. Current renderer bundles still omit
`gpt-5.6-sol:max` from the compact Work power sequence, so an enabled,
catalog-supported Max effort appears in Advanced but not on that slider.

This patch only adds the missing static power entry. Older revisions of Patch P
also bypassed `enabledReasoningEfforts`, which made the Max setting ineffective.
If that legacy marker is present, this patch restores the settings-controlled
filter while updating the power sequence.
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


POWER_MARKER = "/*P:sol-max*/"
LEGACY_FILTER_MARKER = "/*P:max-filter*/"
SOL_MAX_ID = "id:`gpt-5.6-sol:max`"

POWER_PATTERN = re.compile(
    r"(?P<xhigh>\{id:`gpt-5\.6-sol:xhigh`,model:`gpt-5\.6-sol`,"
    r"modelLabel:`(?P<label>[^`]+)`,reasoningEffort:`xhigh`\}),"
    r"(?P<ultra>\{id:`gpt-5\.6-sol:ultra`,model:`gpt-5\.6-sol`,"
    r"modelLabel:`(?P=label)`,reasoningEffort:`ultra`\})"
)

POWER_SPLIT_PATTERN = re.compile(
    r"(?P<xhigh>\{id:`gpt-5\.6-sol:xhigh`,model:`gpt-5\.6-sol`,"
    r"modelLabel:`(?P<label>[^`]+)`,reasoningEffort:`xhigh`\})"
    r"(?P<array_end>\])"
    r"(?=,(?P<ultra_var>[A-Za-z_$][A-Za-z0-9_$]*)="
    r"\{id:`gpt-5\.6-sol:ultra`,model:`gpt-5\.6-sol`,"
    r"modelLabel:`(?P=label)`,reasoningEffort:`ultra`\})"
)

LEGACY_FILTER_PATTERN = re.compile(
    r"\.filter\(\(\{reasoningEffort:(?P<effort>[A-Za-z_$][A-Za-z0-9_$]*)\}\)=>"
    r"(?P<validator>[A-Za-z_$][A-Za-z0-9_$]*)\((?P=effort)\)&&"
    r"\((?P<enabled>[A-Za-z_$][A-Za-z0-9_$]*)\.has\((?P=effort)\)\|\|"
    r"(?P=effort)===`max`\)/\*P:max-filter\*/\)"
)


def patch_power(text: str):
    if SOL_MAX_ID in text:
        return text, False

    match = POWER_PATTERN.search(text)
    if match is not None:
        label = match.group("label")
        max_entry = (
            f"{POWER_MARKER}{{id:`gpt-5.6-sol:max`,model:`gpt-5.6-sol`,"
            f"modelLabel:`{label}`,reasoningEffort:`max`}}"
        )
        replacement = f"{match.group('xhigh')},{max_entry},{match.group('ultra')}"
        return text[: match.start()] + replacement + text[match.end() :], True

    split_match = POWER_SPLIT_PATTERN.search(text)
    if split_match is not None:
        label = split_match.group("label")
        max_entry = (
            f"{POWER_MARKER}{{id:`gpt-5.6-sol:max`,model:`gpt-5.6-sol`,"
            f"modelLabel:`{label}`,reasoningEffort:`max`}}"
        )
        replacement = f"{split_match.group('xhigh')},{max_entry}{split_match.group('array_end')}"
        return text[: split_match.start()] + replacement + text[split_match.end() :], True

    raise RuntimeError(
        "Could not find the gpt-5.6-sol power sequence "
        "(adjacent xhigh/ultra or split xhigh array plus ultra entry)"
    )


def restore_legacy_filter(text: str):
    if LEGACY_FILTER_MARKER not in text:
        return text, False

    match = LEGACY_FILTER_PATTERN.search(text)
    if match is None:
        raise RuntimeError("Found the legacy Patch P marker but could not restore its effort filter")

    effort = match.group("effort")
    validator = match.group("validator")
    enabled = match.group("enabled")
    replacement = (
        f".filter(({{reasoningEffort:{effort}}})=>{validator}({effort})&&"
        f"{enabled}.has({effort}))"
    )
    return text[: match.start()] + replacement + text[match.end() :], True


def find_targets(asar: Path):
    header, payload_start = read_header(asar)
    targets = []
    for path, meta in walk(header):
        if not (path.startswith("webview/assets/") and path.endswith(".js") and "offset" in meta):
            continue
        text = extract(asar, payload_start, meta).decode("utf-8", "replace")
        if "model-and-reasoning-dropdown-" in path and "gpt-5.6-sol:xhigh" in text:
            targets.append((path, meta, text, "power"))
        elif "model-list-filter-" in path and LEGACY_FILTER_MARKER in text:
            targets.append((path, meta, text, "legacy_filter"))
    return header, payload_start, targets


def verify(asar: Path):
    _header, _payload_start, targets = find_targets(asar)
    power_paths = []
    legacy_filter_paths = []
    for path, _meta, text, kind in targets:
        if kind == "power" and SOL_MAX_ID in text:
            power_paths.append(path)
        if kind == "legacy_filter" and LEGACY_FILTER_MARKER in text:
            legacy_filter_paths.append(path)

    if not power_paths:
        raise SystemExit("Verification failed: gpt-5.6-sol:max power entry missing")
    if legacy_filter_paths:
        raise SystemExit(
            "Verification failed: legacy Patch P effort-filter bypass is still present in "
            + ", ".join(sorted(set(legacy_filter_paths)))
        )
    return sorted(set(power_paths))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--app-dir", required=True, help="Codex install dir (contains resources/app.asar)")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    app_dir = Path(args.app_dir).resolve()
    asar = app_dir / "resources" / "app.asar"
    if not asar.exists():
        raise SystemExit(f"Missing ASAR: {asar}")

    header, payload_start, targets = find_targets(asar)
    kinds = {kind for _path, _meta, _text, kind in targets}
    if "power" not in kinds:
        raise SystemExit(f"Could not find the Patch P power target; found: {sorted(kinds)}")

    patched_by_path = {}
    restored_filter_paths = []
    scanned = []
    for path, _meta, text, kind in targets:
        scanned.append(path)
        try:
            if kind == "power":
                patched_text, changed = patch_power(text)
            else:
                patched_text, changed = restore_legacy_filter(text)
                if changed:
                    restored_filter_paths.append(path)
        except RuntimeError as exc:
            raise SystemExit(f"{path}: {exc}") from exc
        if changed:
            patched_by_path[path] = patched_text.encode("utf-8")

    if patched_by_path:
        if not args.no_backup:
            backup = asar.with_name("app.asar.bak-before-sol-max-effort")
            if not backup.exists():
                shutil.copy2(asar, backup)
        repack(asar, header, payload_start, patched_by_path)

    power_paths = verify(asar)
    print(
        json.dumps(
            {
                "status": "patched" if patched_by_path else "already_patched",
                "asar": str(asar),
                "scanned": sorted(set(scanned)),
                "patched": sorted(patched_by_path),
                "power_paths": power_paths,
                "restored_filter_paths": sorted(set(restored_filter_paths)),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
