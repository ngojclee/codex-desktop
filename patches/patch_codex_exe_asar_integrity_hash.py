#!/usr/bin/env python3
"""Rewrite the embedded app.asar SHA256 inside Codex.exe / ChatGPT.exe.

Owl Electron builds (detected by resources/owl-electron-app.json) do NOT use the
classic Electron fuse mechanism (patch_codex_electron_fuse.py is a verified no-op
on them). Instead they embed a JSON integrity manifest in the binary:

    [{"file":"resources\\app.asar","alg":"SHA256","value":"<hex hash>"}]

After the asar patches mutate resources/app.asar, that hardcoded hash no longer
matches and the app dies at launch with:

    FATAL: Integrity check failed for asar archive (<expected> vs <actual>)

This patch locates that manifest blob and rewrites the hash to the SHA256 of the
current resources/app.asar. It is safe: the hash string length is fixed (64 hex
chars), so the patch is an in-place overwrite of the same byte count.

If the manifest is absent (e.g. a pure Electron build that relies on the fuse
instead), this patch is a no-op.
"""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

# The manifest is a JSON array; we search for this anchor substring.
MANIFEST_ANCHOR = b'[{"file":"resources\\app.asar"'

# Also accept a variant using a single backslash or forward slash.
ANCHOR_VARIANTS = [
    b'[{"file":"resources\\app.asar"',
    b'[{"file":"resources/app.asar"',
    b'[{"file":"resources\\\\app.asar"',
]


def compute_asar_sha256(asar_path: Path) -> str:
    h = hashlib.sha256()
    with asar_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Rewrite embedded app.asar SHA256 in the Codex exe.")
    parser.add_argument("--exe", required=True, help="Path to Codex.exe / ChatGPT.exe")
    parser.add_argument("--app-dir", required=True, help="Extracted app dir containing resources/app.asar")
    parser.add_argument("--no-backup", action="store_true", help="Skip the .bak-before-exe-hash backup")
    args = parser.parse_args()

    exe_path = Path(args.exe).resolve()
    asar_path = Path(args.app_dir).resolve() / "resources" / "app.asar"
    if not exe_path.exists():
        raise SystemExit(f"Missing exe: {exe_path}")
    if not asar_path.exists():
        raise SystemExit(f"Missing asar: {asar_path}")

    data = bytearray(exe_path.read_bytes())

    anchor_pos = -1
    used_variant = None
    for variant in ANCHOR_VARIANTS:
        p = data.find(variant)
        if p >= 0:
            anchor_pos = p
            used_variant = variant
            break

    if anchor_pos < 0:
        print("Skipped: no embedded app.asar integrity manifest found in exe (pure Electron build?). No-op.")
        return

    # From the anchor, find the 'value":"<hash>"' field and the closing '}]'.
    tail = data[anchor_pos:]
    try:
        manifest_text = tail.split(b"]", 1)[0].split(b"[", 1)[1]
        manifest_text = b"[" + manifest_text + b"]"
        manifest = json.loads(manifest_text.decode("utf-8", errors="strict"))
    except Exception as exc:
        raise SystemExit(f"Could not parse embedded manifest near offset {anchor_pos}: {exc}")

    if not isinstance(manifest, list) or not manifest:
        raise SystemExit(f"Embedded manifest is not a non-empty list: {manifest_text[:120]!r}")

    target = next((m for m in manifest if "app.asar" in m.get("file", "")), None)
    if target is None:
        raise SystemExit("Embedded manifest contains no app.asar entry.")

    old_hash = target.get("value", "")
    if len(old_hash) != 64 or any(c not in "0123456789abcdef" for c in old_hash):
        raise SystemExit(f"Embedded hash has unexpected format: {old_hash!r}")

    new_hash = compute_asar_sha256(asar_path)
    if old_hash == new_hash:
        print(f"Already correct: app.asar SHA256 in exe already matches ({new_hash[:12]}...). No-op.")
        return

    # In-place overwrite of the 64-hex hash within the binary.
    old_bytes = old_hash.encode("ascii")
    new_bytes = new_hash.encode("ascii")
    idx = data.find(old_bytes)
    if idx < 0:
        raise SystemExit("Embedded hash string not found at expected location; aborting to be safe.")
    if data[idx : idx + 64] != old_bytes:
        raise SystemExit("Hash location mismatch; aborting to be safe.")

    if not args.no_backup:
        backup = exe_path.with_name(exe_path.name + ".bak-before-exe-hash")
        if not backup.exists():
            shutil.copy2(exe_path, backup)

    data[idx : idx + 64] = new_bytes
    exe_path.write_bytes(bytes(data))

    # Verify
    verify = exe_path.read_bytes()
    assert new_bytes in verify and old_bytes not in verify, "Verification failed after write."
    print(f"Patched embedded app.asar SHA256 in {exe_path.name}:")
    print(f"  old: {old_hash}")
    print(f"  new: {new_hash}")
    print(f"  (computed from {asar_path.name}, {asar_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
