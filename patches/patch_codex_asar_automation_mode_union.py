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

# Anchor on structure, never on minified names. Identifiers such as `sWn` and
# the discriminated-union builder `zh` are regenerated on every upstream
# rebuild, and name-based anchors are precisely what broke release
# `v26.903.61454-patched-automation-pipe-z2`. What stays stable is the schema's
# own enum literals, `view` and `delete`, because the tool contract hands those
# strings to the model and cannot rename them without breaking callers.
VIEW_MEMBER_PATTERN = re.compile(
    rf"(?P<id>{IDENT})={IDENT}\(\{{mode:{IDENT}\(`view`\)"
)
DELETE_MEMBER_PATTERN = re.compile(
    rf"(?P<id>{IDENT})={IDENT}\(\{{mode:{IDENT}\(`delete`\)"
)
# A discriminated union over `mode` whose members are bare schema identifiers.
# Requiring bare identifiers rejects look-alikes such as the annotation-mode
# union, which inlines `n.ql({mode:...})` object literals instead.
MODE_UNION_PATTERN = re.compile(
    rf"(?P<target>{IDENT})={IDENT}\(`mode`,\[(?P<members>{IDENT}(?:,{IDENT})+)\]\)"
)
# The flattened chain this patch produces, and the form upstream may ship.
FLAT_UNION_PATTERN = re.compile(
    rf"(?P<target>{IDENT})=(?P<head>{IDENT})((?:\.or\({IDENT}\))+)"
)
FLAT_LINK_PATTERN = re.compile(rf"\.or\(({IDENT})\)")


def automation_member_ids(text: str):
    """Return the view/delete member ids, or None if this chunk is not the
    automation schema."""
    view = VIEW_MEMBER_PATTERN.search(text)
    delete = DELETE_MEMBER_PATTERN.search(text)
    if view is None or delete is None:
        return None
    if view.group("id") == delete.group("id"):
        return None
    return view.group("id"), delete.group("id")


def find_upstream_mode_unions(text: str):
    """Automation `mode` discriminated unions that still need flattening."""
    member_ids = automation_member_ids(text)
    if member_ids is None:
        return []
    view_id, delete_id = member_ids
    found = []
    for match in MODE_UNION_PATTERN.finditer(text):
        names = match.group("members").split(",")
        if view_id in names and delete_id in names:
            found.append(
                {
                    "target": match.group("target"),
                    "members": names,
                    "start": match.start(),
                    "end": match.end(),
                }
            )
    return found


def find_flat_mode_unions(text: str):
    """Automation `mode` unions already written as a plain `.or()` chain."""
    member_ids = automation_member_ids(text)
    if member_ids is None:
        return []
    view_id, delete_id = member_ids
    found = []
    for match in FLAT_UNION_PATTERN.finditer(text):
        names = [match.group("head")] + FLAT_LINK_PATTERN.findall(match.group(3))
        if match.group("head") != view_id or delete_id not in names:
            continue
        end = match.end()
        found.append(
            {
                "target": match.group("target"),
                "members": names,
                "start": match.start(),
                "end": end,
                "marked": text[end:end + len(PATCH_MARKER)] == PATCH_MARKER,
            }
        )
    return found


def mode_union_state(text: str):
    """Classify one renderer chunk for Patch Y.

    `upstream_safe` means the shipped bundle already flattens both mode unions,
    so the embedded-Zod defect cannot occur and Patch Y must not touch it.
    `indeterminate` is always a release blocker, never a silent pass.
    """
    upstream = find_upstream_mode_unions(text)
    flat = find_flat_mode_unions(text)
    marked = [entry for entry in flat if entry["marked"]]
    unmarked = [entry for entry in flat if not entry["marked"]]
    if upstream:
        state = "unpatched"
    elif len(marked) == 2 and not unmarked:
        state = "already_patched"
    elif len(unmarked) == 2 and not marked:
        state = "upstream_safe"
    else:
        state = "indeterminate"
    return {
        "state": state,
        "upstream_count": len(upstream),
        "marked_count": len(marked),
        "unmarked_count": len(unmarked),
        "members": automation_member_ids(text),
    }


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
        # Membership in this set is decided by the schema's own enum literals,
        # so an upstream rename still lands here instead of being reported as
        # "unions not found".
        if automation_member_ids(text) is not None:
            targets.append((path, meta, text))
    return header, payload_start, targets


def patch_text(text: str):
    state = mode_union_state(text)
    if state["state"] == "already_patched":
        return text, False
    if state["state"] == "upstream_safe":
        return text, False

    if state["members"] is None:
        raise RuntimeError(
            "Patch Y cannot classify the automation mode unions: this chunk "
            "carries neither the `view`/`delete` member schemas nor a flattened "
            "union, so shipping it would leave the fix absent without saying so."
        )
    view_id, delete_id = state["members"]
    upstream = find_upstream_mode_unions(text)
    if upstream and (state["marked_count"] or state["unmarked_count"]):
        raise RuntimeError(
            "Patch Y sees discriminated and flattened automation unions at "
            f"once (upstream={state['upstream_count']} "
            f"marked={state['marked_count']} unmarked={state['unmarked_count']}): "
            "a previous run was likely interrupted mid-replacement"
        )
    if not upstream:
        raise RuntimeError(
            "Patch Y cannot classify the automation mode unions: "
            f"state={state['state']} upstream={state['upstream_count']} "
            f"marked={state['marked_count']} unmarked={state['unmarked_count']} "
            f"(view member {view_id!r}, delete member {delete_id!r})"
        )
    if len(upstream) != 2:
        raise RuntimeError(
            "Expected exactly two automation mode discriminated unions, "
            f"found {len(upstream)}: {[entry['target'] for entry in upstream]}. "
            "The renderer schema shape moved; refuse to ship without the fix."
        )

    # Apply right to left so earlier match offsets stay valid. Member order is
    # preserved verbatim from the source, so no name is ever assumed.
    replacements = []
    for entry in upstream:
        chain = entry["members"][0] + "".join(f".or({name})" for name in entry["members"][1:])
        replacements.append((entry["start"], entry["end"], f"{entry['target']}={chain}" + PATCH_MARKER))

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
    states = {
        path: mode_union_state(text) for path, _meta, text in targets
    }
    if not states:
        raise SystemExit(
            "Verification failed: no renderer chunk carries the automation "
            "mode schema (no `mode` union with `view` and `delete` members)"
        )

    unpatched_paths = sorted(p for p, s in states.items() if s["state"] == "unpatched")
    indeterminate = sorted(p for p, s in states.items() if s["state"] == "indeterminate")
    marked_chunks = sorted(p for p, s in states.items() if s["marked_count"])
    marker_count = sum(s["marked_count"] for s in states.values())
    unmarked_count = sum(s["unmarked_count"] for s in states.values())
    upstream_count = sum(s["upstream_count"] for s in states.values())

    if unpatched_paths:
        raise SystemExit(
            "Verification failed: upstream automation mode unions remain: "
            f"{unpatched_paths}"
        )
    if indeterminate:
        raise SystemExit(
            f"Verification failed: automation mode unions indeterminate in {indeterminate}"
        )
    # Either our own replacement is present in both unions, or upstream already
    # shipped both flattened. Anything else is an unverifiable bundle.
    if not (marker_count == 2 and len(marked_chunks) == 1) and unmarked_count != 2:
        raise SystemExit(
            "Verification failed: Patch Y outcome unverifiable "
            f"(marker_count={marker_count} marked_chunks={len(marked_chunks)} "
            f"already-flat={unmarked_count})"
        )

    # Syntax-check every automation schema chunk, patched or not, so a bundle
    # that needs no edit is still proven parseable by this patch's own gate.
    errors = syntax_errors([(path, text) for path, _meta, text in targets])
    if errors:
        raise SystemExit(
            "Verification failed: Patch Y syntax errors:\n"
            + "\n".join(f"  - {error}" for error in errors)
        )
    return {
        "marker_paths": marked_chunks,
        "marker_count": marker_count,
        "unpatched_paths": unpatched_paths,
        "indeterminate_paths": indeterminate,
        "upstream_safe": marker_count == 0 and unmarked_count == 2,
        "syntax_errors": errors,
    }


def _drift_report(asar: Path) -> str:
    """Short, structural description of what the bundle actually contains.

    When the automation schema cannot be located, a bare "not found" message
    costs a full CI round trip to diagnose. Reporting the candidate `mode`
    union call sites and the enum literals that do exist lets the next anchor
    be written from a single failed run. Only minified application code is
    echoed, never user data, and every snippet is truncated.
    """
    header, payload_start = read_header(asar)
    chunks = 0
    mode_union_count = 0
    literal_hits = dict.fromkeys(("view", "delete", "create", "suggested_create"), 0)
    samples = []
    for path, meta in walk(header):
        if not (
            path.startswith("webview/assets/")
            and path.endswith(".js")
            and "offset" in meta
        ):
            continue
        chunks += 1
        text = extract(asar, payload_start, meta).decode("utf-8", "replace")
        for key in literal_hits:
            literal_hits[key] += text.count(f"`{key}`")
        for match in re.finditer(r"[A-Za-z_$][A-Za-z0-9_$]*=\w+\(`mode`,\[", text):
            mode_union_count += 1
            if len(samples) < 3:
                start = match.start()
                samples.append(f"{path}: {text[start:start + 160]}")
    lines = [
        f"scanned webview/assets/*.js chunks: {chunks}",
        f"`mode` discriminated-union call sites: {mode_union_count}",
        "mode enum literal counts: "
        + ", ".join(f"{key}={value}" for key, value in literal_hits.items()),
    ]
    if samples:
        lines.append("first `mode` union shapes:")
        lines.extend(f"  - {sample}" for sample in samples)
    return "\n".join(lines)


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
        raise SystemExit(
            "Could not find the renderer automation mode schema: no "
            "`mode` union carrying `view` and `delete` member schemas exists "
            "in webview/assets/*.js. Patch Y would ship without its fix.\n"
            + _drift_report(asar)
        )

    patched_by_path = {}
    scanned = []
    pre_states = {}
    for path, _meta, text in targets:
        scanned.append(path)
        pre_states[path] = mode_union_state(text)
        try:
            patched_text, changed = patch_text(text)
        except RuntimeError as exc:
            raise SystemExit(f"{path}: {exc}") from exc
        if changed:
            patched_by_path[path] = patched_text.encode("utf-8")

    if patched_by_path:
        status = "patched"
    elif any(s["state"] == "upstream_safe" for s in pre_states.values()):
        status = "upstream_safe"
    else:
        status = "already_patched"

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
                "status": status,
                "asar": str(asar),
                "scanned": sorted(set(scanned)),
                "patched": sorted(patched_by_path),
                "pre_state": {path: state["state"] for path, state in pre_states.items()},
                **result,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
