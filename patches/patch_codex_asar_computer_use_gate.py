#!/usr/bin/env python3
"""Patch J - Bypass Statsig feature gates for Computer Use on Windows.

The Codex Desktop renderer checks three Statsig gates before enabling
Computer Use (Any App + Google Chrome) in Settings:

  Gate 1506311413 -> computer_use (Any App)
  Gate 410065390  -> browser_use_external (Google Chrome)
  Gate 410262010  -> browser_use (In-app Browser)

When the server returns false for these gates (region/org restriction),
the UI shows 'Disabled by your organization or unavailable in your region'.

This patch replaces each gate check c(BT+ID+BT) with the boolean
literal !0 (true) padded to the same byte length, so no ASAR repack is needed.

Combined with the env vars in Launch-Codex.ps1:
  BUILD_FLAVOR=dev  -> passes isInternal check
  CODEX_ELECTRON_ENABLE_WINDOWS_COMPUTER_USE=1 -> forces features.computerUse

Idempotent: re-running on an already-patched asar is a no-op.
"""
import argparse
import json
import shutil
from pathlib import Path


GATES = [
    (b"1506311413", "computer_use (Any App)"),
    (b"410065390", "browser_use_external (Google Chrome)"),
    (b"410262010", "browser_use (In-app Browser)"),
]


def make_replacement(gate_id: bytes) -> tuple:
    bt = b"\x60"
    old = b"c(" + bt + gate_id + bt + b")"
    new = b"!0" + b" " * (len(old) - 2)
    assert len(old) == len(new)
    return old, new


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app-dir", required=True,
                    help="Codex install dir (contains resources/app.asar)")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    app = Path(args.app_dir).resolve()
    asar = app / "resources" / "app.asar"
    if not asar.exists():
        raise SystemExit(f"Missing ASAR: {asar}")

    data = asar.read_bytes()
    patched = data
    results = []

    for gate_id, label in GATES:
        old, new = make_replacement(gate_id)
        count = patched.count(old)
        already = patched.count(new) if count == 0 else 0

        if count == 0 and already > 0:
            results.append({"gate": gate_id.decode(), "label": label, "status": "already_patched"})
        elif count == 0:
            results.append({"gate": gate_id.decode(), "label": label, "status": "not_found"})
        else:
            patched = patched.replace(old, new)
            results.append({"gate": gate_id.decode(), "label": label, "status": "patched", "replaced": count})

    changed = patched != data
    if changed:
        if not args.no_backup:
            bk = asar.with_name("app.asar.bak-before-cu-gate")
            if not bk.exists():
                shutil.copy2(asar, bk)
        asar.write_bytes(patched)

    # Verify
    verify = asar.read_bytes()
    for gate_id, _ in GATES:
        old, _ = make_replacement(gate_id)
        if old in verify:
            raise SystemExit(f"Verification failed: gate {gate_id.decode()} still present")

    output = {"status": "patched" if changed else "already_patched", "asar": str(asar), "gates": results}
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
