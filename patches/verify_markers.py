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
import json
import struct
import sys
from pathlib import Path


def _read_asar(app_dir: Path):
    asar = app_dir / "resources" / "app.asar"
    if not asar.exists():
        raise SystemExit(f"Missing asar: {asar}")
    with asar.open("rb") as f:
        prefix = f.read(16)
        _, header_size, _, json_size = struct.unpack("<IIII", prefix)
        raw = f.read(json_size)
    payload_start = 8 + header_size
    header = json.loads(raw.decode("utf-8"))
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


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: verify_markers.py <APP_DIR>")
    app_dir = Path(sys.argv[1]).resolve()

    signals_path, signals_txt = find_signals(app_dir)
    workspace_path, workspace_txt = find_workspace_bundle(app_dir)

    print(f"Signals chunk : {signals_path}  ({len(signals_txt):,} bytes)")
    print(f"Workspace bundle: {workspace_path}  ({len(workspace_txt):,} bytes)")

    checks = (
        ("Patch A — `limit:1000` present", lambda: "limit:1000" in signals_txt, True),
        ("Patch A — residual `limit:50` count == 0", lambda: signals_txt.count("limit:50") == 0, True),
        ("Patch C v3 — `__capV3=2000` marker (always-paginate)", lambda: "__capV3=2000" in signals_txt, True),
        ("Patch C v3 — v2 guard `if(!this.fetchedRecentConversations)` ABSENT", lambda: "if(!this.fetchedRecentConversations)" not in signals_txt, True),
        ("Patch D — `__pdIds` marker", lambda: "__pdIds" in signals_txt, True),
        ("Patch D — `patch_d_cleared` marker", lambda: "patch_d_cleared" in signals_txt, True),
        ("Patch G — SOCKS5 hardcode `socks5h://127.0.0.1:1080` ABSENT", lambda: "socks5h://127.0.0.1:1080" not in workspace_txt, True),
    )

    failed = []
    for label, check_fn, must_pass in checks:
        ok = bool(check_fn())
        status = "OK" if ok else "FAIL"
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
