#!/usr/bin/env python3
"""Disable Electron EnableEmbeddedAsarIntegrityValidation fuse in a copied Codex.exe.

This is required after patching app.asar, because Codex Desktop is built with the
ASAR integrity fuse enabled — any modification to app.asar would otherwise cause
Codex.exe to exit immediately with code -36861.

Reference: https://www.electronjs.org/docs/latest/tutorial/fuses
"""
import argparse
import shutil
from pathlib import Path

SENTINEL = b"dL7pKGdnNz796PbbjQWNKmHXBZaB9tsX"

FUSE_NAMES = [
    "RunAsNode",
    "EnableCookieEncryption",
    "EnableNodeOptionsEnvironmentVariable",
    "EnableNodeCliInspectArguments",
    "EnableEmbeddedAsarIntegrityValidation",
    "OnlyLoadAppFromAsar",
    "LoadBrowserProcessSpecificV8Snapshot",
    "GrantFileProtocolExtraPrivileges",
]
STATE_NAMES = {0x30: "REMOVED", 0x31: "ENABLE", 0x32: "DISABLE", 0x72: "INHERIT"}

FUSE_INTEGRITY_INDEX = 4  # EnableEmbeddedAsarIntegrityValidation


def find_fuse_block(data: bytes) -> int:
    pos = data.find(SENTINEL)
    if pos < 0:
        raise RuntimeError("Electron fuse sentinel not found — not an Electron binary?")
    if data.find(SENTINEL, pos + 1) >= 0:
        raise RuntimeError("Multiple fuse sentinels found; aborting to be safe.")
    return pos + len(SENTINEL)


def main() -> None:
    parser = argparse.ArgumentParser(description="Flip Electron ASAR-integrity fuse on Codex.exe.")
    parser.add_argument("--exe", required=True, help="Absolute path to the patched copy of Codex.exe")
    parser.add_argument("--no-backup", action="store_true", help="Skip the .bak-before-fuse-patch backup")
    args = parser.parse_args()

    exe_path = Path(args.exe).resolve()
    if not exe_path.exists():
        raise SystemExit(f"Missing exe: {exe_path}")

    data = bytearray(exe_path.read_bytes())
    block_start = find_fuse_block(data)
    version = data[block_start]
    count = data[block_start + 1]
    if version != 1:
        raise SystemExit(f"Unsupported fuse schema version: {version}")
    fuses_offset = block_start + 2
    if FUSE_INTEGRITY_INDEX >= count:
        raise SystemExit(f"Binary has only {count} fuses; cannot reach index {FUSE_INTEGRITY_INDEX}")

    target_addr = fuses_offset + FUSE_INTEGRITY_INDEX
    before = data[target_addr]
    if before == 0x30:  # already REMOVED
        print(f"Already patched: fuse[{FUSE_INTEGRITY_INDEX}] {FUSE_NAMES[FUSE_INTEGRITY_INDEX]} = REMOVED")
        return
    if before != 0x31:
        raise SystemExit(
            f"Unexpected fuse value at index {FUSE_INTEGRITY_INDEX}: 0x{before:02x} ({STATE_NAMES.get(before)})"
        )

    if not args.no_backup:
        backup = exe_path.with_name(exe_path.name + ".bak-before-fuse-patch")
        if not backup.exists():
            shutil.copy2(exe_path, backup)

    data[target_addr] = 0x30  # REMOVED
    exe_path.write_bytes(bytes(data))

    after_block = exe_path.read_bytes()
    block2 = find_fuse_block(after_block)
    fuses_after = after_block[block2 + 2 : block2 + 2 + count]
    print(
        f"Patched fuse[{FUSE_INTEGRITY_INDEX}] {FUSE_NAMES[FUSE_INTEGRITY_INDEX]}: "
        f"{STATE_NAMES.get(before)} -> {STATE_NAMES.get(0x30)}"
    )
    print(f"Fuses after: {fuses_after.decode('ascii', errors='replace')}")


if __name__ == "__main__":
    main()
