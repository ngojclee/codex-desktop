#!/usr/bin/env python3
"""Verify that all four patches were applied to a Codex Desktop app dir.

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
import re
import struct
import sys
from pathlib import Path


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
    for path, meta in _walk(header):
        if (
            path.startswith("webview/assets/")
            and "app-server-manager-signals-" in path
            and path.endswith(".js")
        ):
            return path, _extract(asar, payload_start, meta)
    raise SystemExit("Could not find app-server-manager-signals-*.js in app.asar")


def find_workspace_bundle(app_dir: Path):
    asar, payload_start, header = _read_asar(app_dir)
    for path, meta in _walk(header):
        if (
            path.startswith(".vite/build/workspace-root-drop-handler-")
            and path.endswith(".js")
        ):
            return path, _extract(asar, payload_start, meta)
    raise SystemExit("Could not find workspace-root-drop-handler-*.js in app.asar")

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
            if "/*K*/" in text and "sidebarElectron.codexMobileSetupNavLink" in text:
                return path, text
    raise SystemExit("Could not find Patch K marker in Codex mobile sidebar bundle")


def has_statsig_gate_call(app_dir: Path, gate_id: str) -> bool:
    asar, payload_start, header = _read_asar(app_dir)
    pattern = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*\(`" + re.escape(gate_id) + r"`\)")
    for path, meta in _walk(header):
        if path.startswith("webview/assets/") and path.endswith(".js"):
            text = _extract(asar, payload_start, meta)
            if pattern.search(text):
                return True
    return False


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
    workspace_path, workspace_txt = find_workspace_bundle(app_dir)
    patch_h_path, patch_h_txt = find_patch_h_bundle(app_dir)
    patch_k_path, patch_k_txt = find_patch_k_bundle(app_dir)

    print(f"App version   : {app_version or 'unknown'}")
    print(f"Signals chunk : {signals_path}  ({len(signals_txt):,} bytes)")
    print(f"Workspace bundle: {workspace_path}  ({len(workspace_txt):,} bytes)")
    print(f"Patch H bundle: {patch_h_path}  ({len(patch_h_txt):,} bytes)")
    print(f"Patch K bundle: {patch_k_path}  ({len(patch_k_txt):,} bytes)")
    if args.upstream_tag:
        print(f"Upstream tag  : {args.upstream_tag}")
    if not expect_patch_d:
        print("Patch D expectation: skipped for 26.513.x due to renderer regression mitigation")

    checks = (
        ("Patch A — `limit:1000` present", lambda: "limit:1000" in signals_txt, True),
        ("Patch A — residual `limit:50` count == 0", lambda: signals_txt.count("limit:50") == 0, True),
        ("Patch C v3 — `__capV3=2000` marker (always-paginate)", lambda: "__capV3=2000" in signals_txt, True),
        ("Patch C v3 — v2 guard `if(!this.fetchedRecentConversations)` ABSENT", lambda: "if(!this.fetchedRecentConversations)" not in signals_txt, True),
        ("Patch D — `__pdIds` marker", lambda: "__pdIds" in signals_txt, expect_patch_d),
        ("Patch D — `patch_d_cleared` marker", lambda: "patch_d_cleared" in signals_txt, expect_patch_d),
        ("Patch G — SOCKS5 hardcode `socks5h://127.0.0.1:1080` ABSENT", lambda: "socks5h://127.0.0.1:1080" not in workspace_txt, True),
        ("Patch H — directive Windows path sanitizer marker", lambda: "__PATCH_H_DIRECTIVE_WINDOWS_PATH__" in patch_h_txt, True),
        ("Patch K — Codex mobile sidebar gate marker", lambda: "/*K*/" in patch_k_txt, True),
        ("Patch K — remote-control visibility Statsig call absent", lambda: not has_statsig_gate_call(app_dir, "1042620455"), True),
        ("Patch K — Codex mobile onboarding Statsig call absent", lambda: not has_statsig_gate_call(app_dir, "2798711298"), True),
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
