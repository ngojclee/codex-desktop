#!/usr/bin/env python3
"""Rewrite the embedded app.asar integrity hash inside Codex.exe / ChatGPT.exe.

Owl Electron builds (detected by resources/owl-electron-app.json) do NOT use the
classic Electron fuse mechanism (patch_codex_electron_fuse.py is a verified no-op
on them). Instead they embed a JSON integrity manifest in the binary:

    [{"file":"resources\\app.asar","alg":"SHA256","value":"<hex hash>"}]

After the asar patches mutate resources/app.asar, that hardcoded hash no longer
matches and the app dies at launch with:

    FATAL: Integrity check failed for asar archive (<expected> vs <actual>)

VERIFIED ALGORITHM (2026-09-04, against build 26.901.20858):
The value the app reports as `<actual>` is SHA256 of the asar *header JSON blob*,
NOT the SHA256 of the whole resources/app.asar file, and NOT a header `integrity`
field (this build's header has no `integrity` section at all).

    expected(embedded in exe) == sha256(header JSON) == actual(app computed)

Measured proof on 10.11.1.1:
    sha256(whole app.asar) = 11d282a8...   (does NOT match what the app wants)
    sha256(header JSON)    = 53f5e962...   (matches, app launches with this)

Because patches A..U are same-length byte edits inside the *content* region, the
header is normally untouched, so this hash is stable across patch runs and B2
becomes a no-op on an already-correct exe. It still runs last so a repacked or
re-headered asar gets the right value embedded.

Header layout (verified on 26.901 Owl builds):
    [0]  uint32 = 4               outer size-pickle payload size
    [4]  uint32 = header_size     aligned size of the inner header pickle
    [8]  uint32 = inner pickle payload size
    [12] uint32 = json_len
    [16] json_len bytes of header JSON  <- hashed

It is safe: the hash string length is fixed (64 hex chars), so the patch is an
in-place overwrite of the same byte count.

If the manifest is absent (e.g. a pure Electron build that relies on the fuse
instead), this patch is a no-op.
"""
import argparse
import hashlib
import json
import shutil
import struct
from pathlib import Path

# The manifest is a JSON array; we search for this anchor substring.
MANIFEST_ANCHOR = b'[{"file":"resources\\app.asar"'

# Also accept a variant using a single backslash or forward slash.
ANCHOR_VARIANTS = [
    b'[{"file":"resources\\app.asar"',
    b'[{"file":"resources/app.asar"',
    b'[{"file":"resources\\\\app.asar"',
]


def compute_asar_header_sha256(asar_path: Path) -> str:
    """SHA256 of the asar header JSON blob — the value the app validates.

    Raises SystemExit if the header layout looks wrong, so CI fails loudly
    instead of silently embedding a bad hash.
    """
    with asar_path.open("rb") as f:
        head = f.read(16)
    if len(head) != 16:
        raise SystemExit(f"asar too small to hold a header: {asar_path}")
    _u0, _header_size, _payload_size, json_len = struct.unpack_from("<4I", head, 0)
    if json_len <= 0 or json_len > (1 << 30):
        raise SystemExit(
            f"implausible asar header json_len={json_len} in {asar_path}; "
            "asar layout changed — re-verify before releasing."
        )
    h = hashlib.sha256()
    with asar_path.open("rb") as f:
        f.seek(16)
        remaining = json_len
        while remaining:
            chunk = f.read(min(1 << 20, remaining))
            if not chunk:
                raise SystemExit("asar truncated while reading header JSON")
            h.update(chunk)
            remaining -= len(chunk)
    return h.hexdigest()


def find_manifest_anchor(data: bytes) -> int:
    for variant in ANCHOR_VARIANTS:
        p = data.find(variant)
        if p >= 0:
            return p
    return -1


def patch_one_exe(exe_path: Path, new_hash: str, no_backup: bool) -> str:
    """Rewrite the embedded app.asar hash in one exe.

    Returns "no-manifest" when this exe carries no integrity manifest, otherwise
    "patched" or "already-correct".
    """
    data = bytearray(exe_path.read_bytes())
    anchor_pos = find_manifest_anchor(bytes(data))
    if anchor_pos < 0:
        return "no-manifest"

    tail = data[anchor_pos:]
    try:
        manifest_text = tail.split(b"]", 1)[0].split(b"[", 1)[1]
        manifest_text = b"[" + manifest_text + b"]"
        manifest = json.loads(manifest_text.decode("utf-8", errors="strict"))
    except Exception as exc:
        raise SystemExit(f"{exe_path.name}: could not parse embedded manifest near offset {anchor_pos}: {exc}")

    if not isinstance(manifest, list) or not manifest:
        raise SystemExit(f"{exe_path.name}: embedded manifest is not a non-empty list: {manifest_text[:120]!r}")

    target = next((m for m in manifest if "app.asar" in m.get("file", "")), None)
    if target is None:
        raise SystemExit(f"{exe_path.name}: embedded manifest contains no app.asar entry.")

    old_hash = target.get("value", "")
    if len(old_hash) != 64 or any(c not in "0123456789abcdef" for c in old_hash):
        raise SystemExit(f"{exe_path.name}: embedded hash has unexpected format: {old_hash!r}")

    if old_hash == new_hash:
        return "already-correct"

    # In-place overwrite of the 64-hex hash within the binary.
    old_bytes = old_hash.encode("ascii")
    new_bytes = new_hash.encode("ascii")
    idx = data.find(old_bytes)
    if idx < 0:
        raise SystemExit(f"{exe_path.name}: embedded hash string not found; aborting to be safe.")
    if data[idx : idx + 64] != old_bytes:
        raise SystemExit(f"{exe_path.name}: hash location mismatch; aborting to be safe.")

    if not no_backup:
        backup = exe_path.with_name(exe_path.name + ".bak-before-exe-hash")
        if not backup.exists():
            shutil.copy2(exe_path, backup)

    data[idx : idx + 64] = new_bytes
    exe_path.write_bytes(bytes(data))

    verify = exe_path.read_bytes()
    assert new_bytes in verify and old_bytes not in verify, f"{exe_path.name}: verification failed after write."
    print(f"  {exe_path.name}: {old_hash} -> {new_hash}")
    return "patched"


def main() -> None:
    parser = argparse.ArgumentParser(description="Rewrite the embedded app.asar header hash in the Owl shell exe.")
    parser.add_argument("--exe", required=True, help="Path to Codex.exe / ChatGPT.exe")
    parser.add_argument("--app-dir", required=True, help="Extracted app dir containing resources/app.asar")
    parser.add_argument("--no-backup", action="store_true", help="Skip the .bak-before-exe-hash backup")
    args = parser.parse_args()

    exe_path = Path(args.exe).resolve()
    app_dir = Path(args.app_dir).resolve()
    asar_path = app_dir / "resources" / "app.asar"
    if not exe_path.exists():
        raise SystemExit(f"Missing exe: {exe_path}")
    if not asar_path.exists():
        raise SystemExit(f"Missing asar: {asar_path}")

    new_hash = compute_asar_header_sha256(asar_path)

    # Owl builds keep the integrity manifest in the shell wrapper (ChatGPT.exe on
    # 26.901), while apply-all-patches.ps1 passes the small Codex.exe stub, which
    # carries none. Scanning every top-level exe makes the patch land wherever the
    # manifest actually lives instead of silently no-oping and shipping a build
    # that dies at launch with "Integrity check failed for asar archive".
    candidates = [exe_path] + sorted(app_dir.glob("*.exe"))
    ordered = []
    seen = set()
    for cand in candidates:
        resolved = cand.resolve()
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)
        ordered.append(resolved)

    results = {}
    for cand in ordered:
        results[cand.name] = patch_one_exe(cand, new_hash, args.no_backup)

    if not any(status != "no-manifest" for status in results.values()):
        print("Skipped: no embedded app.asar integrity manifest in any top-level exe "
              "(pure Electron build?). No-op.")
        return

    print(f"Target app.asar header hash (sha256 of header JSON, "
          f"{asar_path.stat().st_size} byte archive): {new_hash}")
    for name, status in results.items():
        print(f"  {name}: {status}")


if __name__ == "__main__":
    main()
