#!/usr/bin/env python3
"""Patch J - Bypass Statsig feature gates for Computer Use on Windows.

The Codex Desktop renderer checks Statsig gates before enabling Computer Use.
Gate IDs: 1506311413 (Any App), 410065390 (Chrome), 410262010 (Browser).

This patch uses a flexible regex to match any single-char function call wrapping
the gate ID (e.g. c(`ID`), l(`ID`), C(`ID`)) and replaces with !0 (true) padded
to the same byte length. No ASAR repack needed.

Idempotent: re-running on an already-patched asar is a no-op.
"""
import argparse
import json
import re
import shutil
from pathlib import Path


GATES = [
    (b"1506311413", "computer_use (Any App)"),
    (b"410065390", "browser_use_external (Google Chrome)"),
    (b"410262010", "browser_use (In-app Browser)"),
]

BT = b"\x60"


def make_pattern(gate_id: bytes):
    return re.compile(rb"[a-zA-Z_]\(" + re.escape(BT + gate_id + BT) + rb"\)")


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
        pattern = make_pattern(gate_id)
        matches = list(pattern.finditer(patched))

        if not matches:
            # Check if already patched (gate ID preceded by !0)
            if gate_id in patched:
                results.append({"gate": gate_id.decode(), "label": label, "status": "pattern_changed"})
            else:
                results.append({"gate": gate_id.decode(), "label": label, "status": "already_patched"})
            continue

        for m in matches:
            old = m.group()
            new = b"!0" + b" " * (len(old) - 2)
            patched = patched.replace(old, new, 1)
        results.append({"gate": gate_id.decode(), "label": label, "status": "patched", "replaced": len(matches)})

    changed = patched != data
    if changed:
        if not args.no_backup:
            bk = asar.with_name("app.asar.bak-before-cu-gate")
            if not bk.exists():
                shutil.copy2(asar, bk)
        asar.write_bytes(patched)

    # Verify no raw gate patterns remain
    verify = asar.read_bytes()
    for gate_id, _ in GATES:
        pat = make_pattern(gate_id)
        if pat.search(verify):
            raise SystemExit(f"Verification failed: gate {gate_id.decode()} still present")

    output = {"status": "patched" if changed else "already_patched", "asar": str(asar), "gates": results}
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
