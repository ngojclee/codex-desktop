#!/usr/bin/env python3
"""Verify that all release patches were applied to a Codex Desktop app dir.

Usage:
    python verify_markers.py <APP_DIR>

Where <APP_DIR> contains `resources/app.asar`. Exits 0 if all markers are
present and no residual `limit:50` is left; exits 1 with a clear message on
the first failure.

Used by the GitHub Actions workflow to gate releases — if patches silently
no-op'd (e.g. upstream changed minifier output and a pattern no longer
matches), the verification fails loudly here instead of shipping a broken
zip.
"""
import argparse
import json
import os
import re
import struct
import sys
from pathlib import Path

PATCH_M_MARKER = "/*M*/maxPayload:1024*1024*1024"
PATCH_O_MARKERS = (
    "if(s?(t.has(n.model)||!n.hidden):!n.hidden)",
    "if(u?(n.has(r.model)||!r.hidden):!r.hidden)",
)
PATCH_O_OLDS = (
    "if(s?t.has(n.model):!n.hidden)",
    "if(u?n.has(r.model):!r.hidden)",
)
WS_CONSTRUCTOR_PATTERN = re.compile(
    r"new\s+(?P<ctor>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"\(this\.options\.websocketUrl,\{(?P<body>[^{}]*?perMessageDeflate:!1[^{}]*?)\}\)"
)
WS_OPTIONS_VAR_PATTERN = re.compile(
    r"(?P<options_var>[A-Za-z_$][A-Za-z0-9_$]*)=\{"
    r"(?P<body>(?=[^;]{0,2500}headers:)[^;]{0,2500}?perMessageDeflate:!1[^;{}]{0,500})"
    r"\},[^;]{0,800}?new\s+[A-Za-z_$][A-Za-z0-9_$]*"
    r"\(this\.options\.websocketUrl,(?:[A-Za-z_$][A-Za-z0-9_$]*,)?(?P=options_var)\)"
)


def _read_asar(app_dir: Path):
    asar = app_dir / "resources" / "app.asar"
    if not asar.exists():
        raise SystemExit(f"Missing asar: {asar}")
    with asar.open("rb") as f:
        prefix = f.read(16)
        first, header_size, _pickle_payload_size, json_size = struct.unpack("<IIII", prefix)
        if first != 4 or json_size <= 0:
            raise SystemExit(f"Unexpected ASAR header prefix: {(first, header_size, _pickle_payload_size, json_size)}")
        raw = f.read(json_size)
        header = json.loads(raw.decode("utf-8"))
    payload_start = 8 + header_size
    return asar, payload_start, header


def _walk(node, parts=()):
    for name, meta in node.get("files", {}).items():
        cp = parts + (name,)
        if "files" in meta:
            yield from _walk(meta, cp)
        else:
            yield "/".join(cp), meta


def _extract(asar: Path, payload_start: int, meta: dict) -> str:
    with asar.open("rb") as f:
        f.seek(payload_start + int(meta["offset"]))
        return f.read(int(meta["size"])).decode("utf-8", "replace")


def find_signals(app_dir: Path):
    asar, payload_start, header = _read_asar(app_dir)
    candidates = []
    for path, meta in _walk(header):
        if (
            path.startswith("webview/assets/")
            and "app-server-manager-signals-" in path
            and path.endswith(".js")
        ):
            return path, _extract(asar, payload_start, meta)
        if path.startswith("webview/assets/") and path.endswith(".js") and "offset" in meta:
            text = _extract(asar, payload_start, meta)
            if (
                "__capV3=2000" in text
                and "__pdIds" in text
                and "getHistoryLimit" in text
                and "markAllConversationsNeedResumeAfterReconnect" in text
            ):
                candidates.append((path, text))
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise SystemExit(f"Multiple recent state chunks found: {[p for p, _ in candidates]}")
    raise SystemExit("Could not find recent state renderer chunk in app.asar")


def find_js_occurrences(app_dir: Path, needle: str):
    asar, payload_start, header = _read_asar(app_dir)
    hits = []
    for path, meta in _walk(header):
        if path.endswith(".js") and "offset" in meta:
            text = _extract(asar, payload_start, meta)
            if needle in text:
                hits.append(path)
    return hits


def websocket_max_payload_status(app_dir: Path):
    asar, payload_start, header = _read_asar(app_dir)
    marker_paths = []
    unpatched_paths = []
    target_paths = []
    for path, meta in _walk(header):
        if not (path.endswith(".js") and "offset" in meta):
            continue
        text = _extract(asar, payload_start, meta)
        if PATCH_M_MARKER in text:
            marker_paths.append(path)
        if "this.options.websocketUrl" not in text or "perMessageDeflate:!1" not in text:
            continue
        for match in WS_CONSTRUCTOR_PATTERN.finditer(text):
            body = match.group("body")
            if "headers:" not in body:
                continue
            target_paths.append(path)
            if "maxPayload:" not in body:
                unpatched_paths.append(path)
        for match in WS_OPTIONS_VAR_PATTERN.finditer(text):
            body = match.group("body")
            target_paths.append(path)
            if "maxPayload:" not in body:
                unpatched_paths.append(path)
    return {
        "marker_paths": sorted(set(marker_paths)),
        "target_paths": sorted(set(target_paths)),
        "unpatched_paths": sorted(set(unpatched_paths)),
    }


def find_patch_h_bundle(app_dir: Path):
    asar, payload_start, header = _read_asar(app_dir)
    for path, meta in _walk(header):
        if path.startswith("webview/assets/") and path.endswith(".js"):
            text = _extract(asar, payload_start, meta)
            if "__PATCH_H_DIRECTIVE_WINDOWS_PATH__" in text:
                return path, text
    raise SystemExit("Could not find Patch H marker in webview assets")


def find_patch_k_bundle(app_dir: Path):
    asar, payload_start, header = _read_asar(app_dir)
    for path, meta in _walk(header):
        if path.startswith("webview/assets/") and path.endswith(".js"):
            text = _extract(asar, payload_start, meta)
            if "/*K*/" in text and (
                "sidebarElectron.codexMobileSetupNavLink" in text
                or "codex.profileFooter.codexMobileTooltip" in text
                or "codex.profileFooter.codexMobileAriaLabel" in text
                or "remote-connection-visibility-" in path
            ):
                return path, text
    raise SystemExit("Could not find Patch K marker in Codex mobile entrypoint bundle")


def model_availability_filter_status(app_dir: Path):
    asar, payload_start, header = _read_asar(app_dir)
    marker_paths = []
    unpatched_paths = []
    candidate_paths = []
    for path, meta in _walk(header):
        if not (path.startswith("webview/assets/") and path.endswith(".js") and "offset" in meta):
            continue
        text = _extract(asar, payload_start, meta)
        if "model-list-filter" in path or "availableModels" in text or "useHiddenModels" in text:
            candidate_paths.append(path)
        if any(marker in text for marker in PATCH_O_MARKERS):
            marker_paths.append(path)
        if any(old in text for old in PATCH_O_OLDS):
            unpatched_paths.append(path)
    return {
        "candidate_paths": sorted(set(candidate_paths)),
        "marker_paths": sorted(set(marker_paths)),
        "unpatched_paths": sorted(set(unpatched_paths)),
    }


def has_statsig_gate_call(app_dir: Path, gate_id: str) -> bool:
    asar, payload_start, header = _read_asar(app_dir)
    pattern = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*\(`" + re.escape(gate_id) + r"`\)")
    for path, meta in _walk(header):
        if path.startswith("webview/assets/") and path.endswith(".js"):
            text = _extract(asar, payload_start, meta)
            if pattern.search(text):
                return True
    return False


def computer_use_plugin_status(app_dir: Path):
    plugin = (
        app_dir
        / "resources"
        / "plugins"
        / "openai-bundled"
        / "plugins"
        / "computer-use"
    )
    if not plugin.exists():
        return {"present": False, "escaped_scopes": [], "sky_package_exists": False}

    node_modules = plugin / "node_modules"
    escaped = []
    for root, dirs, _files in os.walk(plugin):
        for name in dirs:
            if "%40" in name:
                escaped.append(str((Path(root) / name).relative_to(plugin)))
    escaped.sort()
    return {
        "present": True,
        "escaped_scopes": escaped,
        "sky_package_exists": (node_modules / "@oai" / "sky" / "package.json").exists(),
        "node_modules_present": node_modules.exists(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('app_dir')
    parser.add_argument('--upstream-tag', default='')
    args = parser.parse_args()

    app_dir = Path(args.app_dir).resolve()
    version_file = app_dir / 'version'
    app_version = version_file.read_text(encoding='utf-8').strip() if version_file.exists() else ''
    expect_patch_d = not args.upstream_tag.startswith('v26.513.')

    signals_path, signals_txt = find_signals(app_dir)
    socks5_paths = find_js_occurrences(app_dir, "socks5h://127.0.0.1:1080")
    ws_payload = websocket_max_payload_status(app_dir)
    patch_h_path, patch_h_txt = find_patch_h_bundle(app_dir)
    patch_k_path, patch_k_txt = find_patch_k_bundle(app_dir)
    patch_o = model_availability_filter_status(app_dir)
    computer_use = computer_use_plugin_status(app_dir)

    print(f"App version   : {app_version or 'unknown'}")
    print(f"Signals chunk : {signals_path}  ({len(signals_txt):,} bytes)")
    print(f"Patch G SOCKS occurrences: {len(socks5_paths)}")
    for path in socks5_paths:
        print(f"  - {path}")
    print(f"Patch M WS payload marker paths: {len(ws_payload['marker_paths'])}")
    for path in ws_payload["marker_paths"]:
        print(f"  - {path}")
    if ws_payload["unpatched_paths"]:
        print("Patch M unpatched WS targets:")
        for path in ws_payload["unpatched_paths"]:
            print(f"  - {path}")
    print(f"Patch H bundle: {patch_h_path}  ({len(patch_h_txt):,} bytes)")
    print(f"Patch K bundle: {patch_k_path}  ({len(patch_k_txt):,} bytes)")
    print(f"Patch O model filter marker paths: {len(patch_o['marker_paths'])}")
    for path in patch_o["marker_paths"]:
        print(f"  - {path}")
    if patch_o["unpatched_paths"]:
        print("Patch O unpatched model filters:")
        for path in patch_o["unpatched_paths"]:
            print(f"  - {path}")
    print(f"Computer Use plugin: {'present' if computer_use['present'] else 'absent'}")
    if computer_use["present"]:
        print(f"  escaped package folders: {', '.join(computer_use['escaped_scopes']) or '(none)'}")
        print(f"  @oai/sky package: {'present' if computer_use['sky_package_exists'] else 'missing'}")
    if args.upstream_tag:
        print(f"Upstream tag  : {args.upstream_tag}")
    if not expect_patch_d:
        print("Patch D expectation: skipped for 26.513.x due to renderer regression mitigation")

    checks = (
        (
            "Patch A — expanded history limit bumped to 1000",
            lambda: "limit:1000" in signals_txt or "getHistoryLimit?.()??1000" in signals_txt,
            True,
        ),
        ("Patch A — residual `limit:50` count == 0", lambda: signals_txt.count("limit:50") == 0, True),
        ("Patch C v3 — `__capV3=2000` marker (always-paginate)", lambda: "__capV3=2000" in signals_txt, True),
        ("Patch C v3 — v2 guard `if(!this.fetchedRecentConversations)` ABSENT", lambda: "if(!this.fetchedRecentConversations)" not in signals_txt, True),
        ("Patch D — `__pdIds` marker", lambda: "__pdIds" in signals_txt, expect_patch_d),
        ("Patch D — `patch_d_cleared` marker", lambda: "patch_d_cleared" in signals_txt, expect_patch_d),
        ("Patch G — SOCKS5 hardcode `socks5h://127.0.0.1:1080` ABSENT across JS", lambda: len(socks5_paths) == 0, True),
        ("Patch M — shared WS transport target found", lambda: len(ws_payload["target_paths"]) > 0, True),
        ("Patch M — `maxPayload` marker present", lambda: len(ws_payload["marker_paths"]) > 0, True),
        ("Patch M — no shared WS target missing `maxPayload`", lambda: len(ws_payload["unpatched_paths"]) == 0, True),
        ("Patch H — directive Windows path sanitizer marker", lambda: "__PATCH_H_DIRECTIVE_WINDOWS_PATH__" in patch_h_txt, True),
        ("Patch K — Codex mobile entrypoint gate marker", lambda: "/*K*/" in patch_k_txt, True),
        ("Patch K — remote-control visibility Statsig call absent", lambda: not has_statsig_gate_call(app_dir, "1042620455"), True),
        ("Patch K — Codex mobile onboarding Statsig call absent", lambda: not has_statsig_gate_call(app_dir, "2798711298"), True),
        ("Patch L — no percent-escaped Computer Use package folders", lambda: len(computer_use["escaped_scopes"]) == 0, computer_use["present"]),
        ("Patch L — Computer Use @oai/sky package present", lambda: computer_use["sky_package_exists"] or not computer_use.get("node_modules_present", True), computer_use["present"]),
        ("Patch O — model availability filter marker", lambda: len(patch_o["marker_paths"]) > 0, True),
        ("Patch O — old Statsig-only model filter absent", lambda: len(patch_o["unpatched_paths"]) == 0, True),
    )

    failed = []
    for label, check_fn, must_pass in checks:
        ok = bool(check_fn())
        if must_pass:
            status = "OK" if ok else "FAIL"
        else:
            status = "OK" if ok else "SKIP"
        print(f"  [{status}] {label}")
        if must_pass and not ok:
            failed.append(label)

    if failed:
        print()
        print("FAILED markers:")
        for f in failed:
            print(f"  - {f}")
        raise SystemExit(1)

    print()
    print("All patch markers verified.")


if __name__ == "__main__":
    main()
