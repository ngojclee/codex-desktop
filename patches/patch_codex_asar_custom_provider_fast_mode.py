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
    node_failure_line,
    read_header,
    repack,
    walk,
)


PATCH_MARKER = "/*R:custom-provider-fast*/"
MODERN_UI_MARKER = "/*R:service-tier-access*/"
MODERN_REQUEST_MARKER = "/*R:service-tier-request*/"
IDENT = r"[A-Za-z_$][A-Za-z0-9_$]*"
UPSTREAM_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_$])(?P<allowed>[A-Za-z_$][A-Za-z0-9_$]*)="
    r"(?P<chatgpt>[A-Za-z_$][A-Za-z0-9_$]*)&&"
    r"!(?P<loading>[A-Za-z_$][A-Za-z0-9_$]*)&&"
    r"(?P<requirements>[A-Za-z_$][A-Za-z0-9_$]*)!=null&&"
    r"(?P=requirements)\?\.requirements\?\.featureRequirements\?\.fast_mode!==!1"
)
# 26.1007 shares a speed-capability object between the selector and request
# preparation. Both callers reject API-key auth even though catalog tiers exist.
MODERN_UI_PATTERN = re.compile(
    rf"(?<![A-Za-z0-9_$])(?P<cache>{IDENT})\[(?P<req_slot>\d+)\]!=="
    rf"(?P<requirements>{IDENT})\|\|(?P=cache)\[(?P<load_slot>\d+)\]!=="
    rf"(?P<loading>{IDENT})\|\|(?P=cache)\[(?P<auth_slot>\d+)\]!=="
    rf"(?P<chatgpt>{IDENT})\?\((?P<result>{IDENT})="
    r"(?P=chatgpt)&&!(?P=loading)&&(?P=requirements)!=null\?"
    rf"(?P<helper>{IDENT})\((?P=requirements)\):null,"
    r"(?P=cache)\[(?P=req_slot)\]=(?P=requirements),"
    r"(?P=cache)\[(?P=load_slot)\]=(?P=loading),"
    r"(?P=cache)\[(?P=auth_slot)\]=(?P=chatgpt),"
    r"(?P=cache)\[(?P<result_slot>\d+)\]=(?P=result)\)"
    r":(?P=result)=(?P=cache)\[(?P=result_slot)\]"
)
AUTH_VAR_PATTERN = re.compile(
    rf"(?<![A-Za-z0-9_$])(?P<auth>{IDENT})="
    rf"(?P<host>{IDENT})\?\.authMethod\?\?null"
)
MODERN_REQUEST_PATTERN = re.compile(
    rf"async function (?P<function>{IDENT})\((?P<scope>{IDENT}),(?P<host>{IDENT})\)"
    rf"\{{let (?P<auth>{IDENT})=await (?P<auth_reader>{IDENT})"
    r"\((?P=scope),(?P=host)\);"
    r"if\((?P=auth)!==`chatgpt`&&(?P=auth)!==`personalAccessToken`\)return null;"
    rf"let (?P<requirements>{IDENT})=await (?P<reader>{IDENT})"
    r"\((?P=scope),(?P=host),\{priority:`critical`\}\);"
    rf"return (?P=scope)\.query\.setData\((?P<query>{IDENT}),"
    r"\{authMethod:(?P=auth),hostId:(?P=host)\},(?P=requirements)\),"
    rf"(?P<helper>{IDENT})\((?P=requirements)\)\}}"
)
PATCHED_MODERN_UI_PATTERN = re.compile(
    rf"(?<![A-Za-z0-9_$])(?P<cache>{IDENT})\[(?P<req_slot>\d+)\]!=="
    rf"(?P<requirements>{IDENT})\|\|(?P=cache)\[(?P<load_slot>\d+)\]!=="
    rf"(?P<loading>{IDENT})\|\|(?P=cache)\[(?P<auth_slot>\d+)\]!=="
    rf"(?P<auth>{IDENT})\?\((?P<result>{IDENT})=!(?P=loading)\?"
    rf"\((?P<chatgpt>{IDENT})&&(?P=requirements)!=null\?(?P<helper>{IDENT})"
    r"\((?P=requirements)\):(?P=auth)===`apikey`\?"
    r"\{fast:!0,ultrafast:!0\}:null\):null"
    + re.escape(PATCH_MARKER + MODERN_UI_MARKER)
    + r",(?P=cache)\[(?P=req_slot)\]=(?P=requirements),"
    r"(?P=cache)\[(?P=load_slot)\]=(?P=loading),"
    r"(?P=cache)\[(?P=auth_slot)\]=(?P=auth),"
    r"(?P=cache)\[(?P<result_slot>\d+)\]=(?P=result)\)"
    r":(?P=result)=(?P=cache)\[(?P=result_slot)\]"
)
PATCHED_MODERN_REQUEST_PATTERN = re.compile(
    MODERN_REQUEST_PATTERN.pattern.replace(
        r"if\((?P=auth)!==`chatgpt`",
        r"if\((?P=auth)===`apikey`\)return\{fast:!0,ultrafast:!0\}"
        + re.escape(MODERN_REQUEST_MARKER)
        + r";if\((?P=auth)!==`chatgpt`",
        1,
    )
)


def has_unpatched_gate(text: str) -> bool:
    return bool(
        UPSTREAM_PATTERN.search(text)
        or MODERN_UI_PATTERN.search(text)
        or MODERN_REQUEST_PATTERN.search(text)
    )


def _modern_auth_var(text: str, match: re.Match) -> str:
    prefix = text[max(0, match.start() - 1200):match.start()]
    candidates = list(AUTH_VAR_PATTERN.finditer(prefix))
    if len(candidates) != 1:
        raise RuntimeError("Expected one authMethod binding near the Fast memo gate")
    binding = candidates[0]
    host = re.escape(binding.group("host"))
    flag = re.escape(match.group("chatgpt"))
    if not re.search(
        rf"(?<![A-Za-z0-9_$]){flag}={host}\?\.authMethod===`chatgpt`"
        rf"\|\|{host}\?\.authMethod===`personalAccessToken`",
        prefix,
    ):
        raise RuntimeError("Fast memo auth binding no longer matches its entitlement flag")
    return binding.group("auth")


def _check_modern_markers(text: str):
    if MODERN_UI_MARKER not in text and MODERN_REQUEST_MARKER not in text:
        return
    if any(text.count(marker) != 1 for marker in (
        PATCH_MARKER, MODERN_UI_MARKER, MODERN_REQUEST_MARKER
    )):
        raise RuntimeError("Incomplete or duplicated modern Fast compatibility patch")
    if has_unpatched_gate(text):
        raise RuntimeError("Unpatched Fast gate remains beside modern patch markers")
    ui = list(PATCHED_MODERN_UI_PATTERN.finditer(text))
    request = list(PATCHED_MODERN_REQUEST_PATTERN.finditer(text))
    if len(ui) != 1 or len(request) != 1:
        raise RuntimeError("Modern Fast patched memo/request shape drifted")
    if (
        _modern_auth_var(text, ui[0]) != ui[0].group("auth")
        or ui[0].group("helper") != request[0].group("helper")
    ):
        raise RuntimeError("Modern Fast patched auth/helper bindings drifted")


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
        if PATCH_MARKER in text or (
            ("fast_mode" in text or "serviceTierAccess:" in text)
            and has_unpatched_gate(text)
        ):
            targets.append((path, meta, text))
    return header, payload_start, targets


def patch_text(text: str):
    if PATCH_MARKER in text:
        _check_modern_markers(text)
        if has_unpatched_gate(text):
            raise RuntimeError("Unpatched Fast gate remains beside patch marker")
        return text, False

    ui_matches = list(MODERN_UI_PATTERN.finditer(text))
    request_matches = list(MODERN_REQUEST_PATTERN.finditer(text))
    if ui_matches or request_matches:
        if len(ui_matches) != 1 or len(request_matches) != 1:
            raise RuntimeError(
                "Expected one modern Fast UI gate and one request gate, "
                f"found {len(ui_matches)} and {len(request_matches)}"
            )
        if UPSTREAM_PATTERN.search(text):
            raise RuntimeError("Found mixed legacy and modern Fast gates")
        ui, request = ui_matches[0], request_matches[0]
        if ui.group("helper") != request.group("helper"):
            raise RuntimeError("Fast UI and request gates no longer share a capability helper")
        auth = _modern_auth_var(text, ui)
        cache, req, loading, flag, result, helper = (
            ui.group(key) for key in (
                "cache", "requirements", "loading", "chatgpt", "result", "helper"
            )
        )
        req_slot, load_slot, auth_slot, result_slot = (
            ui.group(key) for key in ("req_slot", "load_slot", "auth_slot", "result_slot")
        )
        # Cache authMethod rather than the ChatGPT boolean: switching between
        # two non-ChatGPT auth methods must also recompute the capability object.
        ui_replacement = (
            f"{cache}[{req_slot}]!=={req}||{cache}[{load_slot}]!=={loading}"
            f"||{cache}[{auth_slot}]!=={auth}?("
            f"{result}=!{loading}?({flag}&&{req}!=null?{helper}({req}):"
            f"{auth}===`apikey`?{{fast:!0,ultrafast:!0}}:null):null"
            f"{PATCH_MARKER}{MODERN_UI_MARKER},"
            f"{cache}[{req_slot}]={req},{cache}[{load_slot}]={loading},"
            f"{cache}[{auth_slot}]={auth},{cache}[{result_slot}]={result})"
            f":{result}={cache}[{result_slot}]"
        )
        request_text = request.group(0)
        insert_at = request_text.index(";") + 1
        request_replacement = (
            request_text[:insert_at]
            + f"if({request.group('auth')}===`apikey`)"
            + f"return{{fast:!0,ultrafast:!0}}{MODERN_REQUEST_MARKER};"
            + request_text[insert_at:]
        )
        patched = text
        for match, replacement in sorted(
            [(ui, ui_replacement), (request, request_replacement)],
            key=lambda entry: entry[0].start(), reverse=True,
        ):
            patched = patched[:match.start()] + replacement + patched[match.end():]
        _check_modern_markers(patched)
        return patched, True

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
                errors.append(
    f"{path}: {node_failure_line(result.stderr or result.stdout)}"
                )
    return errors


def status(asar: Path):
    _header, _payload_start, targets = find_targets(asar)
    marker_entries = [
        (path, text) for path, _meta, text in targets if PATCH_MARKER in text
    ]
    unpatched_paths = [
        path for path, _meta, text in targets if has_unpatched_gate(text)
    ]
    layout_errors = []
    for path, text in marker_entries:
        try:
            _check_modern_markers(text)
        except RuntimeError as exc:
            layout_errors.append(f"{path}: {exc}")
    return {
        "marker_paths": sorted(path for path, _text in marker_entries),
        "unpatched_paths": sorted(set(unpatched_paths)),
        "syntax_errors": syntax_errors(marker_entries),
        "layout_errors": layout_errors,
    }


def verify(asar: Path):
    result = status(asar)
    if not result["marker_paths"]:
        raise SystemExit("Verification failed: Patch R marker not found")
    if result["unpatched_paths"]:
        raise SystemExit(
            "Verification failed: upstream Fast auth gates remain: "
            f"{result['unpatched_paths']}"
        )
    if result["syntax_errors"]:
        raise SystemExit(
            "Verification failed: Patch R syntax errors:\n"
            + "\n".join(f"  - {error}" for error in result["syntax_errors"])
        )
    if result["layout_errors"]:
        raise SystemExit(
            "Verification failed: Patch R layout errors:\n"
            + "\n".join(result["layout_errors"])
        )
    return result


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
