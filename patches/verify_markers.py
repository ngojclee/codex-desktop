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
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from patch_codex_asar_model_availability_filter import (
    FILTER_PATTERN as PATCH_O_OLD_PATTERN,
    FILTER_PATTERN_V2 as PATCH_O_OLD_PATTERN_V2,
    FILTER_PATTERN_V3 as PATCH_O_OLD_PATTERN_V3,
    FILTER_PATTERN_V4 as PATCH_O_OLD_PATTERN_V4,
    PATCHED_FILTER_PATTERN as PATCH_O_SAFE_PATTERN,
    PATCHED_FILTER_PATTERN_V2 as PATCH_O_SAFE_PATTERN_V2,
    PATCHED_FILTER_PATTERN_V3 as PATCH_O_SAFE_PATTERN_V3,
    PATCHED_FILTER_PATTERN_V4 as PATCH_O_SAFE_PATTERN_V4,
)
from patch_codex_asar_composer_input_safety import status as patch_u_status
from patch_codex_asar_voice_paste_shortcut import (
    UPSTREAM_SAFE_PATTERN as PATCH_T_UPSTREAM_SAFE_PATTERN,
)

PATCH_J_MARKER = "/*J*/"
PATCH_J_GATES = ("1506311413", "410065390", "410262010")
PATCH_J_CORRUPTED_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_$])[A-Za-z_$][A-Za-z0-9_$]{0,2}!0 {2,}"
)
PATCH_A_RUNTIME_HISTORY_MARKER = "/*A:history-limit*/1000"
SIGNALS_FILENAME_HINTS = (
    "app-server-manager-signals-",
    "codex-micro-slot-signals-",
)
RECENT_STATE_CONTENT_MARKERS = (
    "__capV3=2000",
    "__pdIds",
    "getHistoryLimit",
    "markAllConversationsNeedResumeAfterReconnect",
)
SOCKS_LITERAL = "socks5h://127.0.0.1:1080"
SAFE_SOCKS_LOOPBACK_PATTERN = re.compile(
    r"function\s+[A-Za-z_$][A-Za-z0-9_$]*"
    r"\([^)]*\)\{let\s+(?P<host>[A-Za-z_$][A-Za-z0-9_$]*)="
    r"new URL\([^)]*\)\.hostname;if\(!\("
    r"(?P=host)===`localhost`\|\|(?P=host)===`127\.0\.0\.1`\|\|"
    r"(?P=host)===`\[::1\]`\)\)return\s+[A-Za-z_$][A-Za-z0-9_$]*\}"
)
PATCH_M_MARKER = "/*M*/maxPayload:1024*1024*1024"
PATCH_P_POWER_MARKER = "/*P:sol-max*/"
PATCH_P_LEGACY_FILTER_MARKER = "/*P:max-filter*/"
PATCH_P_SOL_MAX_ID = "id:`gpt-5.6-sol:max`"
PATCH_Q_MARKER = "/*Q:gpt-label*/"
PATCH_Q_OLD_PATTERN = re.compile(r"\.replace\(/\^GPT-/iu,(?:``|\"\"|'')\)")
PATCH_R_MARKER = "/*R:custom-provider-fast*/"
PATCH_R_UPSTREAM_PATTERN = re.compile(
    r"(?P<allowed>[A-Za-z_$][A-Za-z0-9_$]*)="
    r"(?P<chatgpt>[A-Za-z_$][A-Za-z0-9_$]*)&&"
    r"!(?P<loading>[A-Za-z_$][A-Za-z0-9_$]*)&&"
    r"(?P<requirements>[A-Za-z_$][A-Za-z0-9_$]*)!=null&&"
    r"(?P=requirements)\?\.requirements\?\.featureRequirements\?\.fast_mode!==!1"
)
PATCH_S_MARKER = "/*S:apikey-ultra*/"
PATCH_S_UPSTREAM_PATTERN = re.compile(
    r"(?P<result>[A-Za-z_$][A-Za-z0-9_$]*)="
    r"(?P<include>[A-Za-z_$][A-Za-z0-9_$]*)&&"
    r"(?P<getter>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"\(Fh,`1186680773`\)"
)
PATCH_T_MARKER = "/*T:no-voice-paste-binding*/"
PATCH_T_UPSTREAM_PATTERN = re.compile(
    r"(?P<prefix>\{(?:\"id\"|id):(?P<quote>[`'\"])"
    r"composer\.startVoiceMode(?P=quote),"
    r"[^{}]{0,1600}?(?:\"electron\"|electron):\{)"
    r"(?:\"defaultKeybindings\"|defaultKeybindings):"
    r"\[\{(?:\"key\"|key):(?P=quote)Ctrl\+Shift\+V(?P=quote)\}\]"
    r"(?P<suffix>\}\})"
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
    # Upstream renamed the signals chunk between releases (and 26.820 gutted
    # the old one entirely), so rank chunks by recent-state content instead of
    # trusting the filename alone.
    best = None
    for path, meta in _walk(header):
        if not (
            path.startswith("webview/assets/")
            and path.endswith(".js")
            and "offset" in meta
        ):
            continue
        text = _extract(asar, payload_start, meta)
        hits = sum(1 for marker in RECENT_STATE_CONTENT_MARKERS if marker in text)
        named = any(hint in path for hint in SIGNALS_FILENAME_HINTS)
        if hits == 0 and not named:
            continue
        score = hits * 2 + (1 if named else 0)
        if best is None or score > best[0]:
            best = (score, path, text)
    if best is None:
        raise SystemExit("Could not find recent state renderer chunk in app.asar")
    return best[1], best[2]


def find_js_occurrences(app_dir: Path, needle: str):
    asar, payload_start, header = _read_asar(app_dir)
    hits = []
    for path, meta in _walk(header):
        if path.endswith(".js") and "offset" in meta:
            text = _extract(asar, payload_start, meta)
            if needle in text:
                hits.append(path)
    return hits


def socks5_proxy_status(app_dir: Path):
    asar, payload_start, header = _read_asar(app_dir)
    literal_paths = []
    safe_paths = []
    unsafe_paths = []
    for path, meta in _walk(header):
        if not (path.endswith(".js") and "offset" in meta):
            continue
        text = _extract(asar, payload_start, meta)
        if SOCKS_LITERAL not in text:
            continue
        literal_paths.append(path)
        if SAFE_SOCKS_LOOPBACK_PATTERN.search(text):
            safe_paths.append(path)
        else:
            unsafe_paths.append(path)
    return {
        "literal_paths": sorted(set(literal_paths)),
        "safe_paths": sorted(set(safe_paths)),
        "unsafe_paths": sorted(set(unsafe_paths)),
    }


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
                or "codex-mobile-setup-dialog-" in text
                or "[remote-connections/gate-bridge]" in text
                or "remote-connection-visibility-" in path
            ):
                return path, text
    raise SystemExit("Could not find Patch K marker in Codex mobile entrypoint bundle")


def model_availability_filter_status(app_dir: Path):
    asar, payload_start, header = _read_asar(app_dir)
    marker_entries = []
    unpatched_paths = []
    candidate_paths = []
    for path, meta in _walk(header):
        if not (path.startswith("webview/assets/") and path.endswith(".js") and "offset" in meta):
            continue
        text = _extract(asar, payload_start, meta)
        if (
            "model-list-filter" in path
            or ("availableModels" in text and "useHiddenModels" in text)
            or PATCH_O_OLD_PATTERN.search(text)
            or PATCH_O_SAFE_PATTERN.search(text)
            or PATCH_O_OLD_PATTERN_V2.search(text)
            or PATCH_O_SAFE_PATTERN_V2.search(text)
            or PATCH_O_OLD_PATTERN_V3.search(text)
            or PATCH_O_SAFE_PATTERN_V3.search(text)
            or PATCH_O_OLD_PATTERN_V4.search(text)
            or PATCH_O_SAFE_PATTERN_V4.search(text)
        ):
            candidate_paths.append(path)
        if (
            PATCH_O_SAFE_PATTERN.search(text)
            or PATCH_O_SAFE_PATTERN_V2.search(text)
            or PATCH_O_SAFE_PATTERN_V3.search(text)
            or PATCH_O_SAFE_PATTERN_V4.search(text)
        ):
            marker_entries.append((path, text))
        if (
            PATCH_O_OLD_PATTERN.search(text)
            or PATCH_O_OLD_PATTERN_V2.search(text)
            or PATCH_O_OLD_PATTERN_V3.search(text)
            or PATCH_O_OLD_PATTERN_V4.search(text)
        ):
            unpatched_paths.append(path)

    syntax_errors = []
    node = shutil.which("node")
    if marker_entries and node is None:
        syntax_errors.append("node executable not found for Patch O syntax verification")
    elif marker_entries:
        with tempfile.TemporaryDirectory(prefix="codex-patch-o-syntax-") as temp_dir:
            for index, (path, text) in enumerate(marker_entries):
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
                    syntax_errors.append(
                        f"{path}: {detail[-1] if detail else 'node --check failed'}"
                    )

    return {
        "candidate_paths": sorted(set(candidate_paths)),
        "marker_paths": sorted(path for path, _text in marker_entries),
        "unpatched_paths": sorted(set(unpatched_paths)),
        "syntax_errors": syntax_errors,
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


def patch_j_status(app_dir: Path):
    asar, payload_start, header = _read_asar(app_dir)
    gate_patterns = {
        gate_id: re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*\(`" + re.escape(gate_id) + r"`\)")
        for gate_id in PATCH_J_GATES
    }
    gate_paths = {gate_id: [] for gate_id in PATCH_J_GATES}
    marker_entries = []
    corrupted_paths = []

    for path, meta in _walk(header):
        if not (path.startswith("webview/assets/") and path.endswith(".js") and "offset" in meta):
            continue
        text = _extract(asar, payload_start, meta)
        if PATCH_J_MARKER in text:
            marker_entries.append((path, text))
        if PATCH_J_CORRUPTED_PATTERN.search(text):
            corrupted_paths.append(path)
        for gate_id, pattern in gate_patterns.items():
            if pattern.search(text):
                gate_paths[gate_id].append(path)

    syntax_errors = []
    node = shutil.which("node")
    if marker_entries and node is None:
        syntax_errors.append("node executable not found for Patch J syntax verification")
    elif marker_entries:
        with tempfile.TemporaryDirectory(prefix="codex-patch-j-syntax-") as temp_dir:
            for index, (path, text) in enumerate(marker_entries):
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
                    syntax_errors.append(f"{path}: {detail[-1] if detail else 'node --check failed'}")

    return {
        "marker_paths": sorted(path for path, _text in marker_entries),
        "corrupted_paths": sorted(set(corrupted_paths)),
        "gate_paths": {gate_id: sorted(set(paths)) for gate_id, paths in gate_paths.items()},
        "syntax_errors": syntax_errors,
    }


def sol_max_effort_status(app_dir: Path):
    asar, payload_start, header = _read_asar(app_dir)
    marker_entries = []
    power_paths = []
    legacy_filter_paths = []
    candidate_paths = []

    for path, meta in _walk(header):
        if not (path.startswith("webview/assets/") and path.endswith(".js") and "offset" in meta):
            continue
        text = _extract(asar, payload_start, meta)
        if (
            "gpt-5.6-sol:xhigh" in text
            or "model-list-filter-" in path
        ):
            candidate_paths.append(path)
        if (
            "gpt-5.6-sol:xhigh" in text
            and (PATCH_P_POWER_MARKER in text or PATCH_P_SOL_MAX_ID in text)
        ):
            power_paths.append(path)
            if PATCH_P_POWER_MARKER in text:
                marker_entries.append((path, text))
        if "model-list-filter-" in path and PATCH_P_LEGACY_FILTER_MARKER in text:
            legacy_filter_paths.append(path)

    syntax_errors = []
    node = shutil.which("node")
    if marker_entries and node is None:
        syntax_errors.append("node executable not found for Patch P syntax verification")
    elif marker_entries:
        with tempfile.TemporaryDirectory(prefix="codex-patch-p-syntax-") as temp_dir:
            for index, (path, text) in enumerate(marker_entries):
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
                    syntax_errors.append(
                        f"{path}: {detail[-1] if detail else 'node --check failed'}"
                    )

    return {
        "candidate_paths": sorted(set(candidate_paths)),
        "power_paths": sorted(set(power_paths)),
        "legacy_filter_paths": sorted(set(legacy_filter_paths)),
        "syntax_errors": syntax_errors,
    }


def gpt_model_label_status(app_dir: Path):
    asar, payload_start, header = _read_asar(app_dir)
    marker_entries = []
    old_paths = []
    candidate_paths = []
    marker_count = 0

    for path, meta in _walk(header):
        if not (
            path.startswith("webview/assets/")
            and path.endswith(".js")
            and "offset" in meta
        ):
            continue
        text = _extract(asar, payload_start, meta)
        if PATCH_Q_OLD_PATTERN.search(text) or PATCH_Q_MARKER in text:
            candidate_paths.append(path)
        if PATCH_Q_MARKER in text:
            marker_entries.append((path, text))
            marker_count += text.count(PATCH_Q_MARKER)
        if PATCH_Q_OLD_PATTERN.search(text):
            old_paths.append(path)

    syntax_errors = []
    node = shutil.which("node")
    if marker_entries and node is None:
        syntax_errors.append("node executable not found for Patch Q syntax verification")
    elif marker_entries:
        with tempfile.TemporaryDirectory(prefix="codex-patch-q-syntax-") as temp_dir:
            for index, (path, text) in enumerate(marker_entries):
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
                    syntax_errors.append(
                        f"{path}: {detail[-1] if detail else 'node --check failed'}"
                    )

    return {
        "candidate_paths": sorted(set(candidate_paths)),
        "marker_paths": sorted(path for path, _text in marker_entries),
        "marker_count": marker_count,
        "old_paths": sorted(set(old_paths)),
        "syntax_errors": syntax_errors,
    }


def custom_provider_fast_mode_status(app_dir: Path):
    asar, payload_start, header = _read_asar(app_dir)
    marker_entries = []
    unpatched_paths = []

    for path, meta in _walk(header):
        if not (
            path.startswith("webview/assets/")
            and path.endswith(".js")
            and "offset" in meta
        ):
            continue
        text = _extract(asar, payload_start, meta)
        if PATCH_R_MARKER in text:
            marker_entries.append((path, text))
        if PATCH_R_UPSTREAM_PATTERN.search(text):
            unpatched_paths.append(path)

    syntax_errors = []
    node = shutil.which("node")
    if marker_entries and node is None:
        syntax_errors.append("node executable not found for Patch R syntax verification")
    elif marker_entries:
        with tempfile.TemporaryDirectory(prefix="codex-patch-r-syntax-") as temp_dir:
            for index, (path, text) in enumerate(marker_entries):
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
                    syntax_errors.append(
                        f"{path}: {detail[-1] if detail else 'node --check failed'}"
                    )

    return {
        "marker_paths": sorted(path for path, _text in marker_entries),
        "unpatched_paths": sorted(set(unpatched_paths)),
        "syntax_errors": syntax_errors,
    }


def custom_provider_ultra_status(app_dir: Path):
    asar, payload_start, header = _read_asar(app_dir)
    marker_entries = []
    unpatched_paths = []

    for path, meta in _walk(header):
        if not (
            path.startswith("webview/assets/")
            and path.endswith(".js")
            and "offset" in meta
        ):
            continue
        text = _extract(asar, payload_start, meta)
        if PATCH_S_MARKER in text:
            marker_entries.append((path, text))
        if PATCH_S_UPSTREAM_PATTERN.search(text):
            unpatched_paths.append(path)

    syntax_errors = []
    node = shutil.which("node")
    if marker_entries and node is None:
        syntax_errors.append("node executable not found for Patch S syntax verification")
    elif marker_entries:
        with tempfile.TemporaryDirectory(prefix="codex-patch-s-syntax-") as temp_dir:
            for index, (path, text) in enumerate(marker_entries):
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
                    syntax_errors.append(
                        f"{path}: {detail[-1] if detail else 'node --check failed'}"
                    )

    return {
        "marker_paths": sorted(path for path, _text in marker_entries),
        "unpatched_paths": sorted(set(unpatched_paths)),
        "syntax_errors": syntax_errors,
    }


def voice_paste_shortcut_status(app_dir: Path):
    asar, payload_start, header = _read_asar(app_dir)
    marker_entries = []
    safe_entries = []
    unpatched_paths = []

    for path, meta in _walk(header):
        if not (
            path.startswith("webview/assets/")
            and path.endswith(".js")
            and "offset" in meta
        ):
            continue
        text = _extract(asar, payload_start, meta)
        if PATCH_T_MARKER in text:
            marker_entries.append((path, text))
        elif PATCH_T_UPSTREAM_SAFE_PATTERN.search(text):
            safe_entries.append((path, text))
        if PATCH_T_UPSTREAM_PATTERN.search(text):
            unpatched_paths.append(path)

    syntax_errors = []
    node = shutil.which("node")
    syntax_targets = marker_entries + safe_entries
    if syntax_targets and node is None:
        syntax_errors.append("node executable not found for Patch T syntax verification")
    elif syntax_targets:
        with tempfile.TemporaryDirectory(prefix="codex-patch-t-syntax-") as temp_dir:
            for index, (path, text) in enumerate(syntax_targets):
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
                    syntax_errors.append(
                        f"{path}: {detail[-1] if detail else 'node --check failed'}"
                    )

    return {
        "marker_paths": sorted(path for path, _text in marker_entries),
        "upstream_safe_paths": sorted(path for path, _text in safe_entries),
        "unpatched_paths": sorted(set(unpatched_paths)),
        "syntax_errors": syntax_errors,
    }


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
    socks5 = socks5_proxy_status(app_dir)
    ws_payload = websocket_max_payload_status(app_dir)
    patch_j = patch_j_status(app_dir)
    patch_h_path, patch_h_txt = find_patch_h_bundle(app_dir)
    patch_k_path, patch_k_txt = find_patch_k_bundle(app_dir)
    patch_o = model_availability_filter_status(app_dir)
    patch_p = sol_max_effort_status(app_dir)
    patch_q = gpt_model_label_status(app_dir)
    patch_r = custom_provider_fast_mode_status(app_dir)
    patch_s = custom_provider_ultra_status(app_dir)
    patch_t = voice_paste_shortcut_status(app_dir)
    patch_u = patch_u_status(app_dir / "resources" / "app.asar")
    computer_use = computer_use_plugin_status(app_dir)

    print(f"App version   : {app_version or 'unknown'}")
    print(f"Signals chunk : {signals_path}  ({len(signals_txt):,} bytes)")
    print(f"Patch G SOCKS occurrences: {len(socks5['literal_paths'])}")
    for path in socks5["literal_paths"]:
        print(f"  - {path}")
    if socks5["safe_paths"]:
        print("Patch G upstream loopback-safe SOCKS paths:")
        for path in socks5["safe_paths"]:
            print(f"  - {path}")
    if socks5["unsafe_paths"]:
        print("Patch G unsafe SOCKS paths:")
        for path in socks5["unsafe_paths"]:
            print(f"  - {path}")
    print(f"Patch M WS payload marker paths: {len(ws_payload['marker_paths'])}")
    for path in ws_payload["marker_paths"]:
        print(f"  - {path}")
    if ws_payload["unpatched_paths"]:
        print("Patch M unpatched WS targets:")
        for path in ws_payload["unpatched_paths"]:
            print(f"  - {path}")
    print(f"Patch J marker paths: {len(patch_j['marker_paths'])}")
    for path in patch_j["marker_paths"]:
        print(f"  - {path}")
    if patch_j["corrupted_paths"]:
        print("Patch J corrupted token paths:")
        for path in patch_j["corrupted_paths"]:
            print(f"  - {path}")
    if patch_j["syntax_errors"]:
        print("Patch J syntax errors:")
        for error in patch_j["syntax_errors"]:
            print(f"  - {error}")
    print(f"Patch H bundle: {patch_h_path}  ({len(patch_h_txt):,} bytes)")
    print(f"Patch K bundle: {patch_k_path}  ({len(patch_k_txt):,} bytes)")
    print(f"Patch O model filter marker paths: {len(patch_o['marker_paths'])}")
    for path in patch_o["marker_paths"]:
        print(f"  - {path}")
    if patch_o["unpatched_paths"]:
        print("Patch O unpatched model filters:")
        for path in patch_o["unpatched_paths"]:
            print(f"  - {path}")
    if patch_o["syntax_errors"]:
        print("Patch O syntax errors:")
        for error in patch_o["syntax_errors"]:
            print(f"  - {error}")
    print(f"Patch P Sol Max power paths: {len(patch_p['power_paths'])}")
    for path in patch_p["power_paths"]:
        print(f"  - {path}")
    print(f"Patch P legacy max-filter bypass paths: {len(patch_p['legacy_filter_paths'])}")
    for path in patch_p["legacy_filter_paths"]:
        print(f"  - {path}")
    if patch_p["syntax_errors"]:
        print("Patch P syntax errors:")
        for error in patch_p["syntax_errors"]:
            print(f"  - {error}")
    print(f"Patch Q GPT label marker paths: {len(patch_q['marker_paths'])}")
    for path in patch_q["marker_paths"]:
        print(f"  - {path}")
    print(f"Patch Q normalized label calls: {patch_q['marker_count']}")
    if patch_q["old_paths"]:
        print("Patch Q old GPT-prefix stripping paths:")
        for path in patch_q["old_paths"]:
            print(f"  - {path}")
    if patch_q["syntax_errors"]:
        print("Patch Q syntax errors:")
        for error in patch_q["syntax_errors"]:
            print(f"  - {error}")
    print(f"Patch R custom-provider Fast marker paths: {len(patch_r['marker_paths'])}")
    for path in patch_r["marker_paths"]:
        print(f"  - {path}")
    if patch_r["unpatched_paths"]:
        print("Patch R upstream Fast auth gates still present:")
        for path in patch_r["unpatched_paths"]:
            print(f"  - {path}")
    if patch_r["syntax_errors"]:
        print("Patch R syntax errors:")
        for error in patch_r["syntax_errors"]:
            print(f"  - {error}")
    print(f"Patch S API-key Ultra marker paths: {len(patch_s['marker_paths'])}")
    for path in patch_s["marker_paths"]:
        print(f"  - {path}")
    if patch_s["unpatched_paths"]:
        print("Patch S upstream Ultra gates still present:")
        for path in patch_s["unpatched_paths"]:
            print(f"  - {path}")
    if patch_s["syntax_errors"]:
        print("Patch S syntax errors:")
        for error in patch_s["syntax_errors"]:
            print(f"  - {error}")
    print(f"Patch T Voice Mode paste-key marker paths: {len(patch_t['marker_paths'])}")
    for path in patch_t["marker_paths"]:
        print(f"  - {path}")
    if patch_t["upstream_safe_paths"]:
        print("Patch T upstream platform-scoped (Windows already unbound):")
        for path in patch_t["upstream_safe_paths"]:
            print(f"  - {path}")
    if patch_t["unpatched_paths"]:
        print("Patch T conflicting Ctrl+Shift+V bindings still present:")
        for path in patch_t["unpatched_paths"]:
            print(f"  - {path}")
    if patch_t["syntax_errors"]:
        print("Patch T syntax errors:")
        for error in patch_t["syntax_errors"]:
            print(f"  - {error}")
    print(f"Patch U composer safety marker paths: {len(patch_u['marker_paths'])}")
    for path in patch_u["marker_paths"]:
        print(f"  - {path}")
    if patch_u["unpatched_paths"]:
        print("Patch U unpatched composer layouts:")
        for path in patch_u["unpatched_paths"]:
            print(f"  - {path}")
    if patch_u["syntax_errors"]:
        print("Patch U syntax errors:")
        for error in patch_u["syntax_errors"]:
            print(f"  - {error}")
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
            lambda: (
                "limit:1000" in signals_txt
                or "getHistoryLimit?.()??1000" in signals_txt
                or PATCH_A_RUNTIME_HISTORY_MARKER in signals_txt
            ),
            True,
        ),
        (
            "Patch A — legacy capped recent-window shapes absent",
            lambda: (
                "limit:50*this.recentConversationPageCount" not in signals_txt
                and "limit:50,cursor:this.nextRecentConversationCursor" not in signals_txt
            ),
            True,
        ),
        ("Patch C v3 — `__capV3=2000` marker (always-paginate)", lambda: "__capV3=2000" in signals_txt, True),
        ("Patch C v3 — v2 guard `if(!this.fetchedRecentConversations)` ABSENT", lambda: "if(!this.fetchedRecentConversations)" not in signals_txt, True),
        ("Patch D — `__pdIds` marker", lambda: "__pdIds" in signals_txt, expect_patch_d),
        ("Patch D — `patch_d_cleared` marker", lambda: "patch_d_cleared" in signals_txt, expect_patch_d),
        (
            "Patch G — local WebSocket bypasses SOCKS (literal absent or upstream loopback guard present)",
            lambda: len(socks5["unsafe_paths"]) == 0,
            True,
        ),
        ("Patch M — shared WS transport target found", lambda: len(ws_payload["target_paths"]) > 0, True),
        ("Patch M — `maxPayload` marker present", lambda: len(ws_payload["marker_paths"]) > 0, True),
        ("Patch M — no shared WS target missing `maxPayload`", lambda: len(ws_payload["unpatched_paths"]) == 0, True),
        (
            "Patch J — Computer Use Statsig calls absent",
            lambda: all(len(paths) == 0 for paths in patch_j["gate_paths"].values()),
            True,
        ),
        ("Patch J — no corrupted true-literal tokens", lambda: len(patch_j["corrupted_paths"]) == 0, True),
        ("Patch J — touched renderer chunks pass syntax check", lambda: len(patch_j["syntax_errors"]) == 0, True),
        ("Patch H — directive Windows path sanitizer marker", lambda: "__PATCH_H_DIRECTIVE_WINDOWS_PATH__" in patch_h_txt, True),
        ("Patch K — Codex mobile entrypoint gate marker", lambda: "/*K*/" in patch_k_txt, True),
        ("Patch K — remote-control visibility Statsig call absent", lambda: not has_statsig_gate_call(app_dir, "1042620455"), True),
        ("Patch K — Codex mobile onboarding Statsig call absent", lambda: not has_statsig_gate_call(app_dir, "2798711298"), True),
        ("Patch L — no percent-escaped Computer Use package folders", lambda: len(computer_use["escaped_scopes"]) == 0, computer_use["present"]),
        ("Patch L — Computer Use @oai/sky package present", lambda: computer_use["sky_package_exists"] or not computer_use.get("node_modules_present", True), computer_use["present"]),
        ("Patch O — model availability filter marker", lambda: len(patch_o["marker_paths"]) > 0, True),
        ("Patch O — old Statsig-only model filter absent", lambda: len(patch_o["unpatched_paths"]) == 0, True),
        ("Patch O — touched renderer chunks pass syntax check", lambda: len(patch_o["syntax_errors"]) == 0, True),
        ("Patch P — gpt-5.6-sol Max power entry present", lambda: len(patch_p["power_paths"]) > 0, True),
        ("Patch P — built-in reasoning-effort setting remains authoritative", lambda: len(patch_p["legacy_filter_paths"]) == 0, True),
        ("Patch P — touched renderer chunks pass syntax check", lambda: len(patch_p["syntax_errors"]) == 0, True),
        ("Patch Q — GPT label normalization marker", lambda: len(patch_q["marker_paths"]) > 0, True),
        ("Patch Q — no GPT-prefix stripping calls remain", lambda: len(patch_q["old_paths"]) == 0, True),
        ("Patch Q — touched renderer chunks pass syntax check", lambda: len(patch_q["syntax_errors"]) == 0, True),
        ("Patch R — custom-provider Fast selector marker", lambda: len(patch_r["marker_paths"]) > 0, True),
        ("Patch R — upstream Fast auth gate absent", lambda: len(patch_r["unpatched_paths"]) == 0, True),
        ("Patch R — touched renderer chunks pass syntax check", lambda: len(patch_r["syntax_errors"]) == 0, True),
        ("Patch S — API-key Ultra marker", lambda: len(patch_s["marker_paths"]) > 0, True),
        ("Patch S — upstream model-list Ultra gate absent", lambda: len(patch_s["unpatched_paths"]) == 0, True),
        ("Patch S — touched renderer chunks pass syntax check", lambda: len(patch_s["syntax_errors"]) == 0, True),
        (
            "Patch T — Voice Mode paste-key marker or upstream platform-scoped",
            lambda: (
                len(patch_t["marker_paths"]) > 0
                or len(patch_t["upstream_safe_paths"]) > 0
            ),
            True,
        ),
        ("Patch T — conflicting Ctrl+Shift+V binding absent", lambda: len(patch_t["unpatched_paths"]) == 0, True),
        ("Patch T — touched renderer chunks pass syntax check", lambda: len(patch_t["syntax_errors"]) == 0, True),
        ("Patch U — composer safety markers present", lambda: len(patch_u["marker_paths"]) > 0, True),
        ("Patch U — inline Markdown/paste layouts replaced", lambda: len(patch_u["unpatched_paths"]) == 0, True),
        ("Patch U — touched renderer chunks pass syntax check", lambda: len(patch_u["syntax_errors"]) == 0, True),
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
